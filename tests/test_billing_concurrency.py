import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app import main

CALLERS = 32
UNITS = 1
START_USED = 4
RENDEZVOUS_TIMEOUT = 0.25
REPETITIONS = 3
OWNER = {"Authorization": "Bearer owner-token"}


class RendezvousUsage(dict):
    """A USAGE stand-in that parks each caller's first read and store of "used".

    The quota check and the increment in consume_usage sit a few bytecodes
    apart, so a natural interleaving almost never lands. One barrier every
    caller must reach before deciding makes them all compare against the same
    pre-increment value; another every caller must reach before storing pins
    the other end of the window, which is exactly what the race needs.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._local = threading.local()
        self._generation = 0
        self._barrier = threading.Barrier(CALLERS, timeout=RENDEZVOUS_TIMEOUT)
        self._store_barrier = threading.Barrier(CALLERS, timeout=RENDEZVOUS_TIMEOUT)

    def start_generation(self):
        # A barrier that has timed out stays broken, so every repetition needs a
        # fresh one plus a new generation for the per-thread parking to repeat.
        self._generation += 1
        self._barrier = threading.Barrier(CALLERS, timeout=RENDEZVOUS_TIMEOUT)
        self._store_barrier = threading.Barrier(CALLERS, timeout=RENDEZVOUS_TIMEOUT)

    def __getitem__(self, key):
        # Park on the first read of "used" per caller per repetition; the re-read
        # inside the handler's += is the same caller and generation, so it passes.
        value = super().__getitem__(key)
        if key != "used":
            return value
        if getattr(self._local, "generation", None) == self._generation:
            return value
        self._local.generation = self._generation
        try:
            self._barrier.wait()
        except threading.BrokenBarrierError:
            # With the lock held, only one caller is inside the guarded region,
            # so the barrier never fills and the wait times out.
            pass
        return value

    def __setitem__(self, key, value):
        # The store half of the window: parking it too makes a guard that holds
        # only the comparison - still wrong, because the += arithmetic races -
        # fail deterministically, instead of passing whenever the next caller's
        # read lands after the previous caller's store.
        stamp = getattr(self._local, "store_generation", None)
        if key == "used" and stamp != self._generation:
            self._local.store_generation = self._generation
            try:
                self._store_barrier.wait()
            except threading.BrokenBarrierError:
                # With the lock held, one caller at a time reaches this store,
                # so the barrier never fills and the wait times out.
                pass
        super().__setitem__(key, value)

    def stored_used(self):
        # Bypass the hook, or an assertion would park for another timeout.
        return dict.__getitem__(self, "used")


def test_concurrent_consume_never_exceeds_limit(monkeypatch):
    usage = RendezvousUsage(used=START_USED, limit=5)
    monkeypatch.setattr(main, "USAGE", usage)
    limit = dict.__getitem__(usage, "limit")
    client = TestClient(main.app)

    for _ in range(REPETITIONS):
        # Bypass the store hook, or the test thread would park for a timeout.
        dict.__setitem__(usage, "used", START_USED)
        usage.start_generation()
        with ThreadPoolExecutor(max_workers=CALLERS) as pool:
            responses = list(pool.map(
                lambda _: client.post(
                    "/billing/consume", params={"units": UNITS}, headers=OWNER
                ),
                range(CALLERS),
            ))
        accepted = [r for r in responses if r.status_code == 200]
        rejected = [r for r in responses if r.status_code == 429]
        assert usage.stored_used() <= limit
        assert len(accepted) == limit - START_USED
        assert len(rejected) == CALLERS - len(accepted)
        assert all(r.json() == {"detail": "Usage limit reached"} for r in rejected)
        assert usage.stored_used() == START_USED + UNITS * len(accepted)


def test_omitted_units_defaults_to_one():
    # Pins consume_usage's `units: int = 1` default: no other test calls
    # /billing/consume without an explicit units value.
    client = TestClient(main.app)
    # The autouse fixture in tests/conftest.py resets main.USAGE["used"] to 0, so
    # the omitted parameter spends exactly one of the five units.
    response = client.post("/billing/consume", headers=OWNER)
    assert response.status_code == 200
    assert response.json() == {"used": 1, "limit": 5}
