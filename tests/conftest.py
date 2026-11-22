import pytest
from app import main


@pytest.fixture(autouse=True)
def reset_app_state(monkeypatch):
    monkeypatch.setattr(main, "NEXT_PROJECT", 1)
    monkeypatch.setattr(main, "NEXT_TASK", 1)
    main.PROJECTS.clear()
    main.TASKS.clear()
    main.TEAM.clear()
    main.TEAM.update({1: "owner", 2: "member"})
    main.USAGE["used"] = 0
    yield
