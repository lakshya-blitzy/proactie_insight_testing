from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
OWNER = {"Authorization": "Bearer owner-token"}


def test_readiness_reports_storage_check():
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"ready": True, "checks": {"storage": "ok"}}


def test_metrics_reports_request_counter_and_uptime():
    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.json()
    assert body["requests_total"] == 0
    # uptime_seconds is the host's monotonic clock rather than a process duration,
    # so only its type and sign are meaningful here.
    assert isinstance(body["uptime_seconds"], (int, float))
    assert body["uptime_seconds"] >= 0


def test_unknown_token_and_wrong_scheme_are_rejected():
    anonymous = client.get("/projects")
    assert anonymous.status_code == 401
    assert anonymous.json()["detail"] == "Missing bearer token"

    # The guard requires the exact "Bearer " prefix, so a real token behind another
    # scheme is refused by the same branch and reports the same detail.
    wrong_scheme = client.get("/projects", headers={"Authorization": "Token owner-token"})
    assert wrong_scheme.status_code == 401
    assert wrong_scheme.json()["detail"] == "Missing bearer token"

    unknown_token = client.get("/projects", headers={"Authorization": "Bearer not-a-real-token"})
    assert unknown_token.status_code == 401
    assert unknown_token.json()["detail"] == "Invalid token"

    # Positive control: the guard admits a seeded credential, so the refusals above
    # distinguish bad credentials from a guard that rejects everything.
    assert client.get("/projects", headers=OWNER).status_code == 200
