from fastapi.testclient import TestClient
from app.main import app, PROJECTS, TASKS, USAGE

client = TestClient(app)
OWNER = {"Authorization": "Bearer owner-token"}


def setup_function():
    PROJECTS.clear()
    TASKS.clear()
    USAGE["by_user"].clear()


def test_health_endpoint():
    assert client.get("/health").json() == {"status": "ok"}


def test_owner_can_create_and_list_project():
    created = client.post("/projects", json={"name": "Release QA"}, headers=OWNER)
    assert created.status_code == 200
    assert created.json()["name"] == "Release QA"
    assert client.get("/projects", headers=OWNER).json()[0]["id"] == created.json()["id"]


def test_missing_token_is_rejected():
    assert client.get("/projects").status_code == 401


def test_usage_quota_is_isolated_per_principal():
    assert client.post("/billing/consume?units=5", headers=OWNER).json() == {"used": 5, "limit": 5}
    blocked = client.post("/billing/consume?units=1", headers=OWNER)
    assert blocked.status_code == 429
    assert blocked.json() == {"detail": "Usage limit reached"}
    member = {"Authorization": "Bearer member-token"}
    member_ok = client.post("/billing/consume?units=5", headers=member)
    assert member_ok.status_code == 200
    assert member_ok.json() == {"used": 5, "limit": 5}
    assert client.post("/billing/consume?units=1", headers=OWNER).status_code == 429


def test_usage_contract_preserved_for_single_principal():
    member = {"Authorization": "Bearer member-token"}
    first = client.post("/billing/consume", headers=member)
    assert first.status_code == 200
    assert first.json() == {"used": 1, "limit": 5}
    assert list(first.json()) == ["used", "limit"]
    below_floor = client.post("/billing/consume?units=0", headers=member)
    assert below_floor.status_code == 422
    assert below_floor.json() == {"detail": "Units must be positive"}
    assert client.post("/billing/consume?units=5", headers=member).status_code == 429
    unauthenticated = client.post("/billing/consume")
    assert unauthenticated.status_code == 401
    assert unauthenticated.json() == {"detail": "Missing bearer token"}
    unknown_token = client.post("/billing/consume", headers={"Authorization": "Bearer not-a-real-token"})
    assert unknown_token.status_code == 401
    assert unknown_token.json() == {"detail": "Invalid token"}
    malformed_units = client.post("/billing/consume?units=abc", headers=member)
    assert malformed_units.status_code == 422
