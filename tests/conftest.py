import pytest
from app import main


@pytest.fixture(autouse=True)
def reset_app_state(monkeypatch):
    monkeypatch.setattr(main, "NEXT_PROJECT", 1)
    monkeypatch.setattr(main, "NEXT_TASK", 1)
    # Stores are cleared and reseeded in place because the handlers and tests/test_api.py
    # hold these dicts, so a fresh dict would leave their resets on a detached copy; the
    # counters are rebound above because the create handlers reassign them via `global`.
    main.PROJECTS.clear()
    main.TASKS.clear()
    main.TEAM.clear()
    main.TEAM.update({1: "owner", 2: "member"})
    main.USAGE["used"] = 0
    yield
