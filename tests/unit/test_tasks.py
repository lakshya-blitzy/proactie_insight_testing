import pytest
from fastapi import HTTPException

import app.main as main
from app.main import ProjectIn, TaskIn, create_project, create_task, update_task

OWNER = "Bearer owner-token"
MEMBER = "Bearer member-token"


def test_create_task_rejects_unknown_project():
    # Project 1 exists, so a guard that only checks whether PROJECTS is empty raises nothing and
    # fails here. Both absent ids are tried: 2 lies beyond the store's only key, and 0 lies below
    # it, so a guard comparing the requested id against the number of stored projects is caught
    # too — 0 is not greater than len(PROJECTS), so it would fall through to a 200.
    create_project(ProjectIn(name="Alpha"), OWNER)
    for project_id in (2, 0):
        with pytest.raises(HTTPException) as excinfo:
            create_task(TaskIn(title="Ship the release", project_id=project_id), OWNER)
        assert excinfo.value.status_code == 404
        assert excinfo.value.detail == "Project not found"
        assert main.TASKS == {}


def test_create_task_defaults_status_and_stamps_creator():
    project = create_project(ProjectIn(name="Alpha"), OWNER)
    assert project["id"] == 1
    task = create_task(TaskIn(title="Ship the release", project_id=1), OWNER)
    assert task == {"id": 1, "title": "Ship the release", "project_id": 1,
                    "created_by": 1, "status": "open"}
    assert main.TASKS == {1: task}
    # The member creates a task in the owner's project, so created_by can only be 2 when it is
    # stamped from the authenticated caller: the payload's project_id and the project's
    # owner_id are both 1.
    member_task = create_task(TaskIn(title="Ship the follow-up", project_id=1), MEMBER)
    assert member_task == {"id": 2, "title": "Ship the follow-up", "project_id": 1,
                           "created_by": 2, "status": "open"}
    # Both records in one dict: id 2 shows the allocator advanced, and the first task survived.
    assert main.TASKS == {1: task, 2: member_task}


def test_update_task_rejects_unknown_task():
    # The status is invalid on purpose: the task lookup runs before status validation, so a
    # missing id must yield 404 rather than the 422 the same bad status raises for a real task.
    with pytest.raises(HTTPException) as excinfo:
        update_task(9999, "blocked", OWNER)
    assert excinfo.value.status_code == 404
    assert excinfo.value.detail == "Task not found"
    assert main.TASKS == {}


def test_update_task_enforces_status_allow_list():
    create_project(ProjectIn(name="Alpha"), OWNER)
    create_task(TaskIn(title="Ship the release", project_id=1), OWNER)
    with pytest.raises(HTTPException) as excinfo:
        update_task(1, "blocked", OWNER)
    assert excinfo.value.status_code == 422
    assert excinfo.value.detail == "Unsupported status"
    assert main.TASKS[1]["status"] == "open"
    for allowed in ("open", "in_progress", "done"):
        updated = update_task(1, allowed, OWNER)
        # The whole record, so a reshaped or partial return fails here instead of passing on
        # the one field it still carries.
        assert updated == {"id": 1, "title": "Ship the release", "project_id": 1,
                           "created_by": 1, "status": allowed}
        assert main.TASKS[1]["status"] == allowed


def test_update_task_requires_a_valid_token():
    # The task exists before either call on purpose: a handler that never authenticated the
    # caller would find the task and answer 200, so only the credential can cause these
    # rejections.
    create_project(ProjectIn(name="Alpha"), OWNER)
    create_task(TaskIn(title="Ship the release", project_id=1), OWNER)

    with pytest.raises(HTTPException) as missing:
        update_task(1, "done", None)
    assert missing.value.status_code == 401
    assert missing.value.detail == "Missing bearer token"
    assert main.TASKS[1]["status"] == "open"

    with pytest.raises(HTTPException) as unknown:
        update_task(1, "done", "Bearer unknown-token")
    assert unknown.value.status_code == 401
    assert unknown.value.detail == "Invalid token"
    assert main.TASKS[1]["status"] == "open"
