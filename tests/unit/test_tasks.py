import pytest
from fastapi import HTTPException

import app.main as main
from app.main import ProjectIn, TaskIn, create_project, create_task, update_task

OWNER = "Bearer owner-token"


def test_create_task_rejects_unknown_project():
    with pytest.raises(HTTPException) as excinfo:
        create_task(TaskIn(title="Ship the release", project_id=9999), OWNER)
    assert excinfo.value.status_code == 404
    assert excinfo.value.detail == "Project not found"
    assert main.TASKS == {}


def test_create_task_defaults_status_and_stamps_creator():
    project = create_project(ProjectIn(name="Alpha"), OWNER)
    assert project["id"] == 1
    task = create_task(TaskIn(title="Ship the release", project_id=1), OWNER)
    assert task == {"id": 1, "title": "Ship the release", "project_id": 1,
                    "created_by": 1, "status": "open"}
    assert main.TASKS[1] == task


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
        assert updated["status"] == allowed
        assert main.TASKS[1]["status"] == allowed
