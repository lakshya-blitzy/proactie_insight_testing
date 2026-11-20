from fastapi.testclient import TestClient
from app.main import app, PROJECTS, TASKS, USAGE

client = TestClient(app)
OWNER = {"Authorization": "Bearer owner-token"}


def setup_function():
    PROJECTS.clear()
    TASKS.clear()
    USAGE["used"] = 0


def test_health_endpoint():
    assert client.get("/health").json() == {"status": "ok"}


def test_owner_can_create_and_list_project():
    created = client.post("/projects", json={"name": "Release QA"}, headers=OWNER)
    assert created.status_code == 200
    assert created.json()["name"] == "Release QA"
    assert client.get("/projects", headers=OWNER).json()[0]["id"] == created.json()["id"]


def test_missing_token_is_rejected():
    assert client.get("/projects").status_code == 401
