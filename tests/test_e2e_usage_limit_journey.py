from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
OWNER = {"Authorization": "Bearer owner-token"}


def test_usage_limit_is_enforced_across_requests():
    first = client.post("/billing/consume", params={"units": 2}, headers=OWNER)
    assert first.status_code == 200
    assert first.json() == {"used": 2, "limit": 5}

    second = client.post("/billing/consume", params={"units": 3}, headers=OWNER)
    assert second.status_code == 200
    assert second.json() == {"used": 5, "limit": 5}

    refused = client.post("/billing/consume", params={"units": 1}, headers=OWNER)
    assert refused.status_code == 429
    assert refused.json()["detail"] == "Usage limit reached"

    # The refusal body carries no "used" field, so a second refusal is the only
    # way to observe over HTTP that the refused request accumulated nothing.
    refused_again = client.post("/billing/consume", params={"units": 1}, headers=OWNER)
    assert refused_again.status_code == 429
    assert refused_again.json()["detail"] == "Usage limit reached"


def test_non_positive_units_are_rejected():
    zero_units = client.post("/billing/consume", params={"units": 0}, headers=OWNER)
    assert zero_units.status_code == 422
    assert zero_units.json()["detail"] == "Units must be positive"

    negative_units = client.post("/billing/consume", params={"units": -1}, headers=OWNER)
    assert negative_units.status_code == 422
    assert negative_units.json()["detail"] == "Units must be positive"

    # Landing exactly on the ceiling proves the rejected calls accumulated nothing:
    # any accumulation would have pushed this request over the limit instead.
    at_limit = client.post("/billing/consume", params={"units": 5}, headers=OWNER)
    assert at_limit.status_code == 200
    assert at_limit.json() == {"used": 5, "limit": 5}
