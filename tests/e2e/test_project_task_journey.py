import pytest

pytestmark = pytest.mark.e2e

OWNER = {"Authorization": "Bearer owner-token"}
MEMBER = {"Authorization": "Bearer member-token"}


def test_api_client_files_task_in_new_project_and_finds_project_after_task_is_done(api):
    created_project = api.post("/projects", json={"name": "Insight Delivery"}, headers=OWNER)
    assert created_project.status_code == 200
    project = created_project.json()
    assert project["name"] == "Insight Delivery"
    assert project["owner_id"] == 1
    project_id = project["id"]

    created_task = api.post(
        "/tasks",
        json={"title": "Draft the insight report", "project_id": project_id},
        headers=OWNER,
    )
    assert created_task.status_code == 200
    task = created_task.json()
    assert task["title"] == "Draft the insight report"
    assert task["project_id"] == project_id
    assert task["created_by"] == 1
    assert task["status"] == "open"
    task_id = task["id"]

    moved = api.patch(f"/tasks/{task_id}", params={"status": "done"}, headers=OWNER)
    assert moved.status_code == 200
    assert moved.json()["id"] == task_id
    assert moved.json()["project_id"] == project_id
    assert moved.json()["status"] == "done"

    # The API exposes no task-read route, so re-transitioning the same task is the only
    # way to observe that the stored record kept its id, project and done state.
    reread = api.patch(f"/tasks/{task_id}", params={"status": "done"}, headers=OWNER)
    assert reread.status_code == 200
    assert reread.json()["id"] == task_id
    assert reread.json()["project_id"] == project_id
    assert reread.json()["status"] == "done"

    listed = api.get("/projects", headers=OWNER)
    assert listed.status_code == 200
    assert project_id in {entry["id"] for entry in listed.json()}
