"""Regression tests for the atomicity of the metered-billing quota.

``POST /billing/consume`` compares the requested units against the remaining
quota and then adds them to the counter.  When those two statements each read
the shared ``USAGE["used"]`` value independently, concurrent requests all pass
the comparison against the same stale value and all increment, so billable
usage is served beyond the configured ceiling (CWE-367, time-of-check to
time-of-use).

The endpoint now performs the comparison, the increment and the response
snapshot as one indivisible step:

* ``test_concurrent_requests_cannot_drive_usage_above_limit`` forces the exact
  interleaving that used to breach the ceiling and asserts the invariant.
* ``test_usage_limit_is_not_exceeded_under_concurrent_requests`` exercises the
  invariant under real request fan-out against the real counter.
* ``test_rejection_contract_and_body_are_unchanged`` pins the status codes,
  messages and body the repair must leave untouched.
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
# The first read of the counter is held this long for the opposing request.  The
# bounded wait is what keeps a handler that holds the counter for the whole
# check-and-increment from blocking: the opposing request never reaches that
# read, so the barrier breaks and the waiting thread continues.
BARRIER_TIMEOUT = 0.25
# Delay applied to the nth second read of the counter, so whichever thread reads
# second observes the value the other one has already stored.
SECOND_READ_DELAYS = (0.0, 0.15)
REQUEST_TIMEOUT = 30


class _ScheduledUsage(dict):
    """The quota counter, instrumented to shape the schedule of two requests.

    The handler reads ``USAGE["used"]`` twice: once in the quota comparison and
    once in the increment.  This mapping tells those two reads apart per calling
    thread and slows them down just enough to make the raced schedule
    deterministic rather than machine-speed dependent:

    * the first read in a thread parks on a two-party barrier, so both requests
      are approved against the same pre-increment value;
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
                # The opposing request never reached this read, which is what
                # happens once the handler holds the counter across both
                # statements.  Continue with the value already captured.
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
    """
    start = threading.Barrier(count)

    def issue():
        start.wait(timeout=REQUEST_TIMEOUT)
        response = TestClient(app).post(ENDPOINT, params={"units": units}, headers=OWNER)
        return response.status_code

    with ThreadPoolExecutor(max_workers=count) as pool:
        futures = [pool.submit(issue) for _ in range(count)]
        return [future.result(timeout=REQUEST_TIMEOUT) for future in futures]


def test_concurrent_requests_cannot_drive_usage_above_limit(monkeypatch):
    """Two full-ceiling requests may not both be served.

    Both requests are made to read the same pre-increment value, and their
    writes are staggered.  Unless the comparison and the increment are
    indivisible, both are answered ``200`` and the meter lands at ten against a
    ceiling of five.
    """
    usage = _ScheduledUsage()
    monkeypatch.setattr(main_module, "USAGE", usage)

    statuses = _burst(UNITS, CONTENDERS)

    accepted = statuses.count(200)
    observed = (statuses, usage.used(), usage.limit())
    assert set(statuses) <= {200, 429}, observed
    assert accepted * UNITS <= LIMIT, observed
    assert usage.used() <= usage.limit(), observed
    assert usage.used() == accepted * UNITS, observed


def test_usage_limit_is_not_exceeded_under_concurrent_requests():
    """A real burst never serves more billable units than the ceiling allows.

    Ten rounds of a wide one-unit burst and a narrow full-ceiling burst.  Both
    the ceiling and the accounting are asserted, so a rejected request can be
    shown to leave the meter untouched and an accepted burst to be counted in
    full.
    """
    for _ in range(10):
        for units, count in ((1, 32), (5, 8)):
            USAGE["used"] = 0
            statuses = _burst(units, count)
            served = statuses.count(200) * units
            observed = (units, count, statuses, USAGE["used"], LIMIT)
            assert set(statuses) <= {200, 429}, observed
            assert USAGE["used"] <= LIMIT, observed
            assert served <= LIMIT, observed
            assert USAGE["used"] == served, observed


def test_rejection_contract_and_body_are_unchanged():
    """The endpoint's observable contract survives the repair unchanged.

    Authentication, the non-positive rejection, the over-limit rejection and the
    accepted body are pinned through the HTTP surface, with the meter asserted
    untouched on every rejected path.
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
