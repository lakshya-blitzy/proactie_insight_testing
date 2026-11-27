import pytest
from app import main


@pytest.fixture(autouse=True)
def reset_app_state(monkeypatch):
    monkeypatch.setattr(main, "NEXT_PROJECT", 1)
    monkeypatch.setattr(main, "NEXT_TASK", 1)
    # Stores are cleared and reseeded in place because tests/test_api.py imports these
    # dicts directly, so a fresh dict would leave its setup_function clearing a detached
    # copy; the counters are rebound above because the create handlers reassign them via
    # a global statement. TEAM is reseeded because change_role writes it; USERS is left
    # untouched because no handler writes it.
    main.PROJECTS.clear()
    main.TASKS.clear()
    main.TEAM.clear()
    main.TEAM.update({1: "owner", 2: "member"})
    main.USAGE["by_user"].clear()
    yield
