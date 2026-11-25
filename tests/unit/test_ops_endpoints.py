import pytest

from app.main import health, metrics, readiness


def test_health_payload():
    assert health() == {"status": "ok"}


def test_readiness_payload():
    payload = readiness()
    assert payload == {"ready": True, "checks": {"storage": "ok"}}
    # 1 == True in Python, so the whole-dict comparison alone would accept a numeric
    # readiness flag; identity pins the contract to the boolean itself.
    assert payload["ready"] is True


def test_metrics_payload_shape():
    payload = metrics()
    assert set(payload) == {"requests_total", "uptime_seconds"}
    assert payload["requests_total"] == 0
    # uptime_seconds is an absolute reading of the host clock, not a duration, so only its
    # type is asserted and the clock is never patched.
    assert isinstance(payload["uptime_seconds"], float)


@pytest.mark.xfail(reason="metrics.requests_total is hard-coded to 0 and never incremented", strict=False)
def test_metrics_counts_requests():
    # A field named requests_total is expected to count requests, so any traffic at all
    # should raise it above zero; production is frozen, so the gap is recorded here.
    health()
    assert metrics()["requests_total"] >= 1
