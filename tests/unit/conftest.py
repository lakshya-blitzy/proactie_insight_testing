# This nearer conftest redeclares reset_app_state so the unit suite owns its own isolation
# state and never depends on the parent conftest; tests outside tests/unit keep the parent's.

import pytest

from app import main


@pytest.fixture(autouse=True)
def reset_app_state(monkeypatch):
    # create_project and create_task rebind these counters through a `global` statement, so
    # plain assignment would leak between runs; monkeypatch also restores the pre-test value
    # at teardown. The stores are mutated in place and never rebound, so any dict a test
    # module imported directly stays the dict the handlers write to. TEAM is reseeded because
    # change_role writes it; USERS is left untouched because no handler writes it, and
    # USAGE["limit"] keeps its configured value because the quota tests assert against it.
    monkeypatch.setattr(main, "NEXT_PROJECT", 1)
    monkeypatch.setattr(main, "NEXT_TASK", 1)
    main.PROJECTS.clear()
    main.TASKS.clear()
    main.TEAM.clear()
    main.TEAM.update({1: "owner", 2: "member"})
    main.USAGE["used"] = 0
    yield
