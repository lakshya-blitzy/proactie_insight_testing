import pytest
from fastapi import HTTPException

import app.main as main
from app.main import change_role

OWNER = "Bearer owner-token"
MEMBER = "Bearer member-token"


def test_non_owner_cannot_change_role():
    # User 2 targets its own id on purpose: the request is otherwise well formed, so only
    # the caller's role can decide the outcome.
    with pytest.raises(HTTPException) as rejected:
        change_role(2, "owner", MEMBER)
    assert rejected.value.status_code == 403
    assert rejected.value.detail == "Owner role required"
    assert main.TEAM == {1: "owner", 2: "member"}


def test_change_role_rejects_unknown_user_or_role():
    with pytest.raises(HTTPException) as unknown_user:
        change_role(99, "member", OWNER)
    assert unknown_user.value.status_code == 404
    assert unknown_user.value.detail == "User or role not found"

    with pytest.raises(HTTPException) as unknown_role:
        change_role(2, "admin", OWNER)
    assert unknown_role.value.status_code == 404
    assert unknown_role.value.detail == "User or role not found"

    # One guard covers both causes, so both rejections must be side-effect free.
    assert main.TEAM == {1: "owner", 2: "member"}


def test_owner_changes_role_and_team_reflects_it():
    result = change_role(2, "owner", OWNER)

    assert result == {"user_id": 2, "role": "owner"}
    # The whole table, because the response alone cannot show that TEAM was written.
    assert main.TEAM == {1: "owner", 2: "owner"}

    # "member" is an accepted role too (app/main.py:91), not only "owner".
    demoted = change_role(2, "member", OWNER)

    assert demoted == {"user_id": 2, "role": "member"}
    # Demoting after promoting bounds the write at app/main.py:93 in the second direction:
    # a handler that admitted "member" but skipped the write would leave TEAM[2] at "owner".
    assert main.TEAM == {1: "owner", 2: "member"}
