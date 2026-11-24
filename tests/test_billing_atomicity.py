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
# Bound on one attempt at the first-read rendezvous, not on the rendezvous
# itself.  A wait that times out here leaves the barrier broken, and the first
# read then waits for the opposing request to record its own first read, so a
# late request cannot slip past the schedule.
BARRIER_TIMEOUT = 0.25
# Delay applied to the nth second read of the counter, so whichever thread reads
# second observes the value the other one has already stored.
SECOND_READ_DELAYS = (0.0, 0.15)
REQUEST_TIMEOUT = 30
# Safety net on the second phase of the first-read rendezvous, which otherwise
# ends on the fact that the handler already holds its guard.  A healthy run never
# reaches it; it is what stops a request from hanging when the opposing one dies
# before it ever reads the counter.
PEER_ARRIVAL_TIMEOUT = 10


def _handler_guard_held():
    """Report whether the handler's own guard is held, without importing it.

    The repaired handler holds ``USAGE_LOCK`` across the comparison, the
    increment and the response snapshot, so a request sitting on its first read
    of the counter is holding that guard, and every other request is provably
    blocked before its own first read.  That is the only sound reason to stop
    waiting for the opposing request: while the guard is held the peer cannot
    arrive at this read at all.

    A duration is not that reason.  How long the opposing request takes to reach
    its first read depends on how loaded the host is, so no timeout tells a slow
    peer apart from one that can never arrive, and a duration that gave up too
    early would report the rendezvous as forced when it was not.  The wait
    therefore ends on this test, with ``PEER_ARRIVAL_TIMEOUT`` kept only as a
    safety net.

    The guard is looked up on the module rather than imported, so a handler that
    has none — the unsynchronised one this module exists to catch — reports "not
    held" and the opposing request is always awaited.
    """
    guard = getattr(main_module, "USAGE_LOCK", None)
    try:
        return bool(guard is not None and guard.locked())
    except (AttributeError, TypeError):
        return False


class _ScheduledUsage(dict):
    """The quota counter, instrumented to shape the schedule of two requests.

    The handler reads ``USAGE["used"]`` twice: once in the quota comparison and
    once in the increment.  This mapping tells those two reads apart per calling
    thread and orders them so the raced schedule is forced on any host:

    * the first read in a thread captures the value, records that the thread has
      read the counter, and then parks on a two-party barrier, so both requests
      are approved against the same pre-increment value however far apart they
      arrive;
    * a barrier that times out does not abandon that rendezvous: the first read
      then waits for the opposing request to record its own first read, ending
      that wait at once if the handler's guard is already held, because a guarded
      handler cannot let the second request reach this read at all;
    * a wait ended on that guard is checked at the second read: a second read
      made with the guard no longer held shows it never spanned the section, so
      the run records that the schedule was not forced and the test reports it
      rather than passing on it;
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
        self._arrival_lock = threading.Lock()
        self._arrivals = 0
        self._peer_arrived = threading.Event()
        self._unsound_bail = False
        self._second_read_lock = threading.Lock()
        self._second_reads = 0

    def __getitem__(self, key):
        if key != "used":
            return super().__getitem__(key)

        reads = getattr(self._reads, "count", 0) + 1
        self._reads.count = reads
        if reads == 1:
            captured = super().__getitem__(key)
            self._record_arrival()
            try:
                self._barrier.wait()
            except threading.BrokenBarrierError:
                self._await_peer()
            return captured

        if getattr(self._reads, "bailed_on_guard", False) and not _handler_guard_held():
            # The guard that ended this thread's wait for the opposing request
            # does not span this read either, so it never kept that request out
            # of the section: the opposition was as free to arrive here as it is
            # in the unguarded code this module exists to catch.  Record that
            # nothing forced it rather than let the run pass as though the
            # schedule had been shaped.
            self._unsound_bail = True

        with self._second_read_lock:
            index = self._second_reads
            self._second_reads += 1
        if index < len(SECOND_READ_DELAYS):
            time.sleep(SECOND_READ_DELAYS[index])
        return super().__getitem__(key)

    def _record_arrival(self):
        """Note that this thread has read the counter, releasing any peer wait.

        The value is captured before this runs, so a thread that reaches the
        counter late still compares against the value the meter held before
        either request wrote to it.
        """
        with self._arrival_lock:
            self._arrivals += 1
            if self._arrivals >= CONTENDERS:
                self._peer_arrived.set()

    def _await_peer(self):
        """Finish the rendezvous a broken barrier could not, or give it up.

        The wait ends on where the opposing request provably is rather than on
        how long it has taken: once the handler's guard is held no request can
        reach this read, so there is nothing left to wait for, and while it is
        not held the peer is still on its way.  ``PEER_ARRIVAL_TIMEOUT`` is the
        safety net behind that fact, not a schedule of its own.
        """
        if _handler_guard_held():
            self._reads.bailed_on_guard = True
            return
        self._peer_arrived.wait(timeout=PEER_ARRIVAL_TIMEOUT)

    def schedule_forced(self):
        """Whether every wait abandoned on the handler's guard was justified.

        A first read stops waiting for the opposing request when the handler's
        guard is held, on the premise that the guard spans the second read as
        well and so keeps that request out of the whole section.  A handler that
        releases the guard between the two reads excludes nothing, which makes
        the opposing request as free to arrive as it is in the unguarded code
        this module exists to catch, and then the run proves nothing about the
        section.
        """
        return not self._unsound_bail

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
    in: which requests were still running, which had not started, whether the
    handler's guard is held, and the counter as it stood.  It then abandons the
    pool with its outstanding futures cancelled rather than joined.

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
            f"{len(pending)} not yet started at {pending}, the handler's guard "
            f"(USAGE_LOCK) held at the timeout: {_handler_guard_held()}, "
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

    The run also asserts that the schedule was in fact forced, so a handler that
    would let the opposing request reach the read unblocked cannot pass on a
    schedule nothing shaped.
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
    assert usage.schedule_forced(), (
        "the handler released its guard between the comparison and the increment, so the "
        "opposing request was never excluded and the schedule was not forced",
        observed,
    )


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
    """The status codes, messages and body the endpoint answers on each path.

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
