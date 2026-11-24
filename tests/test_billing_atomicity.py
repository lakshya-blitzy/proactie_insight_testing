"""Atomicity of the metered-billing quota.

``POST /billing/consume`` compares the requested units against the remaining
quota and then adds them to the counter, reading the shared ``USAGE["used"]``
value on both statements.  If the comparison and the increment read that value
independently, concurrent requests pass the comparison against the same stale
value and all increment, serving billable usage beyond the configured ceiling
(CWE-367, time-of-check to time-of-use).  The endpoint performs the comparison,
the increment and the response snapshot as one indivisible step:

* ``test_concurrent_requests_cannot_drive_usage_above_limit`` forces the exact
  interleaving that would breach the ceiling and asserts the invariant.
* ``test_usage_limit_is_not_exceeded_under_concurrent_requests`` exercises the
  invariant under real request fan-out against the real counter.
* ``test_rejection_contract_and_body_are_unchanged`` pins the status codes,
  messages and body the endpoint answers with.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import USAGE, app

OWNER = {"Authorization": "Bearer owner-token"}
ENDPOINT = "/billing/consume"
LIMIT = 5
UNITS = 5

# Two requests race for the five-unit ceiling, each asking for all of it.
CONTENDERS = 2
# Bound on one attempt at the first-read rendezvous. The wait ends here when only
# one request reaches the counter read, which is what a handler that serves that
# read exclusively produces, so neither request can hang on the other.
BARRIER_TIMEOUT = 0.25
# Delay applied to the nth second read of the counter, so whichever thread reads
# second observes the value the other one has already stored.
SECOND_READ_DELAYS = (0.0, 0.15)
REQUEST_TIMEOUT = 30


class _ScheduledUsage(dict):
    """The quota counter, instrumented to shape the schedule of two requests.

    The handler reads ``USAGE["used"]`` twice: once in the quota comparison and
    once in the increment.  This mapping tells those two reads apart per calling
    thread and orders them so the raced schedule is forced on any host:

    * the first read in a thread captures the value and then parks on a two-party
      barrier, so both requests are approved against the same pre-increment value
      however far apart they arrive;
    * a barrier wait ends at ``BARRIER_TIMEOUT`` when only one request can reach
      the read, so the waiting thread continues with the value it captured;
    * the second read is staggered, so the thread that reads it later observes
      the value the other thread has already stored.

    Only ``__getitem__`` is overridden.  Leaving ``__iter__``, ``keys``,
    ``items`` and ``get`` alone keeps the handler's ``dict(USAGE)`` snapshot on
    the C fast path, so the snapshot adds no phantom reads to the schedule.
    """

    def __init__(self):
        super().__init__(used=0, limit=LIMIT)
        self._reads = threading.local()
        self._barrier = threading.Barrier(CONTENDERS, timeout=BARRIER_TIMEOUT)
        self._second_read_lock = threading.Lock()
        self._second_reads = 0

    def __getitem__(self, key):
        if key != "used":
            return super().__getitem__(key)

        reads = getattr(self._reads, "count", 0) + 1
        self._reads.count = reads
        if reads == 1:
            captured = super().__getitem__(key)
            try:
                self._barrier.wait()
            except threading.BrokenBarrierError:
                pass
            return captured

        with self._second_read_lock:
            index = self._second_reads
            self._second_reads += 1
        if index < len(SECOND_READ_DELAYS):
            time.sleep(SECOND_READ_DELAYS[index])
        return super().__getitem__(key)

    def used(self):
        """Current meter, read without engaging the schedule hook."""
        return dict.__getitem__(self, "used")

    def limit(self):
        """Configured ceiling, read without engaging the schedule hook."""
        return dict.__getitem__(self, "limit")


@pytest.fixture(autouse=True)
def isolated_meter():
    """Start each test from a fresh meter and restore the shared state after it.

    The suite shares one in-memory counter and the fixture in ``tests/test_api.py``
    does not restore ``USAGE["limit"]``, so both keys are saved here and put back
    whatever the test did to them.
    """
    snapshot = dict(USAGE)
    USAGE["used"] = 0
    USAGE["limit"] = LIMIT
    try:
        yield
    finally:
        USAGE.clear()
        USAGE.update(snapshot)


def _burst(units, count):
    """Issue ``count`` requests for ``units`` each at once; return their statuses.

    Every worker drives its own ``TestClient``: the handler is a synchronous
    ``def``, so FastAPI runs it in a worker thread of the client that issued the
    request, and separate clients put the requests genuinely in flight together.
    Each client and its event-loop portal are built before the gate opens, so the
    only work left between the gate and the handler is the request itself.

    Both the wait for the results and the teardown that follows it are bounded, so
    a handler that stops making progress fails the run here instead of parking the
    suite.  The wait on each future is bounded by ``REQUEST_TIMEOUT``; the request
    itself is bounded by nothing, because httpx sets no timeout over the in-process
    ASGI transport, so a handler that deadlocks inside its guarded section keeps
    its worker inside ``client.post``.  Joining that worker would wait for it
    forever, which is why a wait that runs out describes the state it was taken
    in: which requests were still running, which had not started, and the counter
    as it stood.  It then abandons the pool with its outstanding futures cancelled
    rather than joined.

    One deadlock is still left to the harness: a worker abandoned that way is a
    non-daemon thread, so CPython's interpreter-exit handler joins it once every
    test has finished.  This helper turns a deadlocked handler into a reported
    failure with that state named; it cannot make the deadlocked thread terminate.
    """
    start = threading.Barrier(count)

    def issue():
        with TestClient(app) as client:
            start.wait(timeout=REQUEST_TIMEOUT)
            response = client.post(ENDPOINT, params={"units": units}, headers=OWNER)
            return response.status_code

    pool = ThreadPoolExecutor(max_workers=count)
    collected = False
    try:
        futures = [pool.submit(issue) for _ in range(count)]
        statuses = [future.result(timeout=REQUEST_TIMEOUT) for future in futures]
        collected = True
        return statuses
    except TimeoutError as exc:
        if any(future.done() and future.exception() is exc for future in futures):
            # A worker that raised ``TimeoutError`` itself is re-raised unchanged;
            # only a wait that ran out leaves its future unfinished, and that is
            # the state described below.
            raise
        running = [index for index, future in enumerate(futures) if future.running()]
        pending = [
            index
            for index, future in enumerate(futures)
            if not future.done() and not future.running()
        ]
        meter = getattr(main_module, "USAGE", None)
        snapshot = dict(meter) if isinstance(meter, dict) else meter
        raise AssertionError(
            f"a burst of {count} request(s) for {units} unit(s) did not return "
            f"within {REQUEST_TIMEOUT}s and the handler is not making progress: "
            f"{len(running)} worker(s) still running at {running}, "
            f"{len(pending)} not yet started at {pending}, "
            f"the counter reads {snapshot}"
        ) from None
    finally:
        # A pool whose results are all in is joined exactly as its context manager
        # would join it; a pool that has been given up on is cancelled and left,
        # so a worker parked in a deadlocked handler cannot hold the run here.
        pool.shutdown(wait=collected, cancel_futures=not collected)


def test_concurrent_requests_cannot_drive_usage_above_limit(monkeypatch):
    """Two full-ceiling requests may not both be served.

    Both requests are made to read the same pre-increment value, and their
    writes are staggered.  Unless the comparison and the increment are
    indivisible, both are answered ``200`` and the meter lands at ten against a
    ceiling of five.

    Exactly one request is served, because the first is approved against the
    fresh meter and the second then finds the ceiling reached, so a handler that
    answered every contender with ``429`` cannot pass on an empty meter either.
    """
    usage = _ScheduledUsage()
    monkeypatch.setattr(main_module, "USAGE", usage)

    statuses = _burst(UNITS, CONTENDERS)

    accepted = statuses.count(200)
    observed = (statuses, usage.used(), usage.limit())
    assert set(statuses) <= {200, 429}, observed
    assert accepted * UNITS <= LIMIT, observed
    assert accepted == 1, observed
    assert usage.used() <= usage.limit(), observed
    assert usage.used() == accepted * UNITS, observed


def test_usage_limit_is_not_exceeded_under_concurrent_requests():
    """A real burst serves the ceiling in full and never more than it.

    Ten rounds of a wide one-unit burst and a narrow full-ceiling burst, each
    shaped so the correct handler keeps answering until the meter reaches the
    ceiling.  The units served must therefore equal it, so a handler that
    answers every request ``429`` cannot pass on an idle meter.  The ceiling and
    the accounting are asserted as well, so a rejected request can be shown to
    leave the meter untouched and an accepted burst to be counted in full.
    """
    for _ in range(10):
        for units, count in ((1, 32), (5, 8)):
            USAGE["used"] = 0
            statuses = _burst(units, count)
            served = statuses.count(200) * units
            observed = (units, count, statuses, USAGE["used"], LIMIT)
            assert set(statuses) <= {200, 429}, observed
            assert USAGE["used"] <= LIMIT, observed
            assert served == LIMIT, observed
            assert USAGE["used"] == served, observed


def test_rejection_contract_and_body_are_unchanged():
    """The status codes, messages and body the endpoint answers on each path.

    Authentication, the non-positive rejection, the over-limit rejection and the
    accepted body are pinned through the HTTP surface, with the meter asserted
    untouched on every rejected path.  The meter is asserted after the accepted
    request too, and a request that lands exactly on the ceiling is accepted, so
    neither an increment that never happens nor a comparison that rejected
    equality would survive this test.
    """
    client = TestClient(app)

    for headers, detail in (
        (None, "Missing bearer token"),
        ({"Authorization": "Bearer not-a-token"}, "Invalid token"),
    ):
        response = client.post(ENDPOINT, params={"units": 1}, headers=headers)
        assert response.status_code == 401
        assert response.json()["detail"] == detail
        assert USAGE["used"] == 0

    for units in (0, -1):
        response = client.post(ENDPOINT, params={"units": units}, headers=OWNER)
        assert response.status_code == 422
        assert response.json()["detail"] == "Units must be positive"
        assert USAGE["used"] == 0

    response = client.post(ENDPOINT, params={"units": LIMIT + 1}, headers=OWNER)
    assert response.status_code == 429
    assert response.json()["detail"] == "Usage limit reached"
    assert USAGE["used"] == 0

    response = client.post(ENDPOINT, params={"units": 2}, headers=OWNER)
    assert response.status_code == 200
    assert response.json() == {"used": 2, "limit": LIMIT}
    assert USAGE["used"] == 2

    # A request whose units land exactly on the ceiling must still be accepted,
    # so start it from a fresh meter. The autouse ``isolated_meter`` fixture
    # restores both keys after this test, so zeroing ``used`` here leaves the
    # shared counter exactly as the test found it.
    USAGE["used"] = 0
    response = client.post(ENDPOINT, params={"units": LIMIT}, headers=OWNER)
    assert response.status_code == 200
    assert response.json() == {"used": LIMIT, "limit": LIMIT}
    assert USAGE["used"] == LIMIT
