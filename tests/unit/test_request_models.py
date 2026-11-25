# Bounded request fields are rejected by pydantic at construction, before any handler body
# runs, so every rejection here expects pydantic.ValidationError. FastAPI's 422 translation
# happens only at the ASGI boundary, which these tests deliberately do not cross.

import pytest
from pydantic import ValidationError

from app.main import ProjectIn, TaskIn


def test_project_name_bounds():
    with pytest.raises(ValidationError):
        ProjectIn(name="A")
    with pytest.raises(ValidationError):
        ProjectIn(name="x" * 81)
    assert ProjectIn(name="ab").name == "ab"
    assert ProjectIn(name="x" * 80).name == "x" * 80


def test_task_title_bounds():
    with pytest.raises(ValidationError):
        TaskIn(title="A", project_id=1)
    with pytest.raises(ValidationError):
        TaskIn(title="x" * 121, project_id=1)
    shortest = TaskIn(title="ab", project_id=1)
    assert shortest.title == "ab"
    assert shortest.project_id == 1
    longest = TaskIn(title="x" * 120, project_id=1)
    assert longest.title == "x" * 120
    assert longest.project_id == 1


def test_task_project_id_must_be_integer():
    with pytest.raises(ValidationError):
        TaskIn(title="ab", project_id="not-an-int")
    # app/main.py:22 types project_id as int, so a fractional id must be rejected here at the
    # request contract instead of reaching create_task and only failing there as a 404.
    with pytest.raises(ValidationError):
        TaskIn(title="ab", project_id=1.5)
    parsed = TaskIn(title="ab", project_id=1)
    assert parsed.project_id == 1
    assert type(parsed.project_id) is int
