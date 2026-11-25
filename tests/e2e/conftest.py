import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    # A fixed port would collide with a developer's server on the documented 8000 and
    # with any parallel run of this suite, so let the kernel pick a free one.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def live_server():
    port = _free_port()
    # cwd=REPO_ROOT is what makes "app.main:app" importable in the child, and
    # sys.executable keeps it in the interpreter that satisfied requirements.txt.
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        # The server is a separate process, so an answered request is the only
        # authoritative readiness signal.
        deadline = time.monotonic() + 30
        while True:
            if proc.poll() is not None:
                raise RuntimeError(f"server exited early: {proc.stdout.read()}")
            try:
                if httpx.get(f"{base_url}/health", timeout=1.0).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError(f"server did not answer /health at {base_url} within 30s")
            time.sleep(0.1)
        yield base_url
    finally:
        # A failing journey must not leave a listener behind for the next run.
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


@pytest.fixture(scope="session")
def api(live_server):
    # trust_env=False so a proxy variable cannot capture loopback traffic.
    with httpx.Client(base_url=live_server, timeout=5.0, trust_env=False) as client:
        yield client
