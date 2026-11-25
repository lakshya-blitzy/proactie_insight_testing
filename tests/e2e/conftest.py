import collections
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# The readiness budget, in seconds; the deadline and the failure message both read it.
READINESS_TIMEOUT = 30

# How many free-port -> spawn -> readiness attempts the harness makes before it reports the
# last child's failure; a port another process claims before the child binds is retried.
SPAWN_ATTEMPTS = 3

# How many lines of the child's output are kept for a failure message; the drain drops the
# oldest, so a runaway child cannot grow the buffer without limit.
OUTPUT_LINES = 200

# app/main.py:35-37 answers exactly this; a 200 carrying anything else is a foreign
# responder holding the port, not the service under test.
HEALTH_BODY = {"status": "ok"}


def _free_port() -> int:
    # A fixed port would collide with a developer's server on the documented 8000 and
    # with any parallel run of this suite, so let the kernel pick a free one.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def live_server():
    for attempt in range(1, SPAWN_ATTEMPTS + 1):
        port = _free_port()
        # cwd=REPO_ROOT is what makes "app.main:app" importable in the child, and
        # sys.executable keeps it in the interpreter that satisfied requirements.txt.
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
             "--port", str(port), "--log-level", "warning"],
            cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        # The pipe is drained on its own thread for the session's life: a child that writes
        # more than the OS pipe buffer would otherwise block inside write() and stop serving.
        # The deque is bounded, so the drain costs the harness a fixed amount of memory.
        output = collections.deque(maxlen=OUTPUT_LINES)

        def _drain(stream=proc.stdout, lines=output):
            try:
                for line in stream:
                    lines.append(line)
            except ValueError:
                pass  # teardown closed the pipe while this thread was still reading it

        reader = threading.Thread(target=_drain, name="uvicorn-output", daemon=True)
        reader.start()
        base_url = f"http://127.0.0.1:{port}"
        retry = False
        try:
            # The server is a separate process, so an answered request is the only
            # authoritative readiness signal.
            deadline = time.monotonic() + READINESS_TIMEOUT
            # trust_env=False for the same reason the api client sets it: a proxy variable
            # must not capture this loopback request and turn readiness into a false timeout.
            with httpx.Client(base_url=base_url, timeout=1.0, trust_env=False) as probe:
                while True:
                    if proc.poll() is not None:
                        # The child is gone, so its pipe reaches EOF: joining the reader
                        # bounds this wait, where reading the pipe here would not.
                        reader.join(timeout=5)
                        captured = "".join(output).strip()
                        address_taken = "address already in use" in captured.lower()
                        if address_taken and attempt < SPAWN_ATTEMPTS:
                            # Another process claimed the port after _free_port() released
                            # it; the next attempt picks a fresh one instead of failing.
                            retry = True
                            break
                        raise RuntimeError(f"server exited early: {captured}")
                    try:
                        response = probe.get("/health")
                        if response.status_code == 200 and response.json() == HEALTH_BODY:
                            break
                    except (httpx.HTTPError, ValueError):
                        # Not listening yet, or an answer that is not this service's: json()
                        # raises ValueError on a body that is not JSON.
                        pass
                    if time.monotonic() > deadline:
                        # A live child's pipe never reaches EOF, so report what the drain
                        # thread has already taken off it rather than blocking in a read.
                        raise RuntimeError(
                            f"server did not answer /health at {base_url} within "
                            f"{READINESS_TIMEOUT}s: {''.join(output).strip()}"
                        )
                    time.sleep(0.1)
            if retry:
                continue
            yield base_url
            return
        finally:
            # A failing journey must not leave a listener behind for the next run, and a
            # retried attempt must not leave its child, its pipe or its reader behind.
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            # The child is reaped, so the reader reaches EOF; joining it and closing the pipe
            # is what keeps either from outliving the server it belongs to.
            reader.join(timeout=5)
            proc.stdout.close()


@pytest.fixture(scope="session")
def api(live_server):
    # trust_env=False so a proxy variable cannot capture loopback traffic.
    with httpx.Client(base_url=live_server, timeout=5.0, trust_env=False) as client:
        yield client
