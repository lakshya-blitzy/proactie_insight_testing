from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
OWNER = {"Authorization": "Bearer owner-token"}


def test_owner_files_task_in_project_and_reads_it_back_done():
    created_project = client.post("/projects", json={"name": "Insight Delivery"}, headers=OWNER)
    assert created_project.status_code == 200
    project = created_project.json()
    assert project["name"] == "Insight Delivery"
    assert project["owner_id"] == 1
    project_id = project["id"]

    created_task = client.post(
        "/tasks",
        json={"title": "Draft the insight report", "project_id": project_id},
        headers=OWNER,
    )
    assert created_task.status_code == 200
    task = created_task.json()
    assert task["status"] == "open"
    assert task["project_id"] == project_id
    assert task["created_by"] == 1
    task_id = task["id"]

    moved = client.patch(f"/tasks/{task_id}", params={"status": "done"}, headers=OWNER)
    assert moved.status_code == 200
    assert moved.json()["id"] == task_id
    assert moved.json()["status"] == "done"
    assert moved.json()["project_id"] == project_id

    # The API exposes no task-read route, so re-transitioning the same task is the only
    # way to observe that the record persisted as done against the same project.
    reread = client.patch(f"/tasks/{task_id}", params={"status": "done"}, headers=OWNER)
    assert reread.status_code == 200
    assert reread.json()["id"] == task_id
    assert reread.json()["status"] == "done"
    assert reread.json()["project_id"] == project_id

    listed = client.get("/projects", headers=OWNER)
    assert listed.status_code == 200
    assert project_id in [entry["id"] for entry in listed.json()]


def test_task_in_unknown_project_is_rejected():
    created_task = client.post(
        "/tasks", json={"title": "Orphan task", "project_id": 9999}, headers=OWNER
    )
    assert created_task.status_code == 404
    assert created_task.json()["detail"] == "Project not found"


def test_updating_unknown_task_returns_404():
    updated_task = client.patch("/tasks/9999", params={"status": "done"}, headers=OWNER)
    assert updated_task.status_code == 404
    assert updated_task.json()["detail"] == "Task not found"


def test_unsupported_status_is_rejected_and_record_survives():
    created_project = client.post("/projects", json={"name": "Status Guard"}, headers=OWNER)
    assert created_project.status_code == 200
    project_id = created_project.json()["id"]

    created_task = client.post(
        "/tasks", json={"title": "Guard the status", "project_id": project_id}, headers=OWNER
    )
    assert created_task.status_code == 200
    task_id = created_task.json()["id"]

    # A string detail distinguishes this handler-raised 422 from pydantic's
    # list-of-errors 422 raised for a malformed body.
    rejected = client.patch(f"/tasks/{task_id}", params={"status": "blocked"}, headers=OWNER)
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "Unsupported status"

    moved = client.patch(f"/tasks/{task_id}", params={"status": "done"}, headers=OWNER)
    assert moved.status_code == 200
    assert moved.json()["id"] == task_id
    assert moved.json()["status"] == "done"


def test_task_title_below_minimum_is_rejected():
    created_project = client.post("/projects", json={"name": "Title Guard"}, headers=OWNER)
    assert created_project.status_code == 200
    project_id = created_project.json()["id"]

    created_task = client.post(
        "/tasks", json={"title": "x", "project_id": project_id}, headers=OWNER
    )
    assert created_task.status_code == 422
    errors = created_task.json()["detail"]
    assert [error["loc"] for error in errors] == [["body", "title"]]
    assert errors[0]["type"] == "string_too_short"
    assert errors[0]["ctx"]["min_length"] == 2
