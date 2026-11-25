from fastapi.testclient import TestClient
from app.main import app, PROJECTS, TASKS, TEAM, USAGE, seed_sessions

client = TestClient(app)
OWNER = {"Authorization": "Bearer owner-token"}
MEMBER = {"Authorization": "Bearer member-token"}


def setup_function():
    PROJECTS.clear()
    TASKS.clear()
    TEAM.clear()
    TEAM.update({1: "owner", 2: "member"})
    USAGE["used"] = 0
    USAGE["limit"] = 5
    # Re-issues both demo tokens, so a token revoked or rotated by another
    # test module cannot leak into this one.
    seed_sessions()


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_owner_can_create_and_list_project():
    created = client.post("/projects", json={"name": "Release QA"}, headers=OWNER)
    assert created.status_code == 200
    assert created.json()["name"] == "Release QA"
    assert created.json()["owner_id"] == 1
    mine = client.post("/projects", json={"name": "Member Intake"}, headers=MEMBER)
    assert mine.status_code == 200
    assert mine.json()["owner_id"] == 2

    owner_list = client.get("/projects", headers=OWNER)
    assert owner_list.status_code == 200
    owner_body = owner_list.json()
    assert len(owner_body) == 2
    assert {p["id"] for p in owner_body} == {created.json()["id"], mine.json()["id"]}

    member_list = client.get("/projects", headers=MEMBER)
    assert member_list.status_code == 200
    member_body = member_list.json()
    assert len(member_body) == 1
    assert [p["id"] for p in member_body] == [mine.json()["id"]]


def test_missing_token_is_rejected():
    response = client.get("/projects")
    assert response.status_code == 401
    assert response.json()["detail"] == "Missing bearer token"


def test_billing_consume_validates_units_and_enforces_limit():
    refused = client.post("/billing/consume?units=0", headers=OWNER)
    assert refused.status_code == 422
    assert refused.json()["detail"] == "Units must be positive"
    assert USAGE["used"] == 0

    unit_one = client.post("/billing/consume?units=1", headers=OWNER)
    assert unit_one.status_code == 200
    assert unit_one.json()["used"] == 1
    unit_two = client.post("/billing/consume?units=2", headers=OWNER)
    assert unit_two.status_code == 200
    assert unit_two.json()["used"] == 3
    default_units = client.post("/billing/consume", headers=OWNER)
    assert default_units.status_code == 200
    assert default_units.json()["used"] == 4
    at_limit = client.post("/billing/consume", headers=OWNER)
    assert at_limit.status_code == 200
    assert at_limit.json()["used"] == 5

    blocked = client.post("/billing/consume?units=1", headers=OWNER)
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == "Usage limit reached"
    assert USAGE["used"] == 5


def test_create_task_rejects_unknown_project_and_stores_open():
    orphan = client.post("/tasks", json={"title": "Valid title", "project_id": 999}, headers=OWNER)
    assert orphan.status_code == 404
    assert orphan.json()["detail"] == "Project not found"
    assert TASKS == {}

    project_created = client.post("/projects", json={"name": "Task Home"}, headers=OWNER)
    assert project_created.status_code == 200
    project = project_created.json()
    created = client.post("/tasks", json={"title": "Write tests", "project_id": project["id"]}, headers=MEMBER)
    assert created.status_code == 200
    task = created.json()
    assert task["status"] == "open"
    assert task["created_by"] == 2
    assert task["project_id"] == project["id"]
    assert TASKS[task["id"]] == task


def test_patch_task_status_persists_and_rejects_invalid():
    project_created = client.post("/projects", json={"name": "Patch Home"}, headers=OWNER)
    assert project_created.status_code == 200
    project = project_created.json()
    task_created = client.post("/tasks", json={"title": "Track status", "project_id": project["id"]}, headers=OWNER)
    assert task_created.status_code == 200
    task = task_created.json()
    assert task["status"] == "open"

    progress = client.patch(f"/tasks/{task['id']}?status=in_progress", headers=OWNER)
    assert progress.status_code == 200
    assert progress.json()["status"] == "in_progress"
    assert TASKS[task["id"]]["status"] == "in_progress"

    updated = client.patch(f"/tasks/{task['id']}?status=done", headers=OWNER)
    assert updated.status_code == 200
    assert updated.json()["status"] == "done"
    assert TASKS[task["id"]]["status"] == "done"

    missing = client.patch("/tasks/999?status=done", headers=OWNER)
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Task not found"

    rejected = client.patch(f"/tasks/{task['id']}?status=blocked", headers=OWNER)
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "Unsupported status"
    assert TASKS[task["id"]]["status"] == "done"


def test_owner_role_change_rejects_unknown_user_and_role():
    unknown_user = client.post("/teams/999/role?role=member", headers=OWNER)
    assert unknown_user.status_code == 404
    assert unknown_user.json()["detail"] == "User or role not found"

    bad_role = client.post("/teams/2/role?role=superuser", headers=OWNER)
    assert bad_role.status_code == 404
    assert bad_role.json()["detail"] == "User or role not found"
    assert TEAM == {1: "owner", 2: "member"}


def test_role_change_requires_owner_caller():
    denied = client.post("/teams/2/role?role=owner", headers=MEMBER)
    assert denied.status_code == 403
    assert denied.json()["detail"] == "Owner role required"
    assert TEAM[2] == "member"

    granted = client.post("/teams/2/role?role=owner", headers=OWNER)
    assert granted.status_code == 200
    assert granted.json() == {"user_id": 2, "role": "owner"}
    assert TEAM[2] == "owner"
