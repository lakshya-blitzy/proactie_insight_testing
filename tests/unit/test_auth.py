import pytest
from fastapi import HTTPException

from app.main import current_user

OWNER = "Bearer owner-token"
MEMBER = "Bearer member-token"


def test_missing_authorization_raises_missing_token():
    with pytest.raises(HTTPException) as exc_info:
        current_user(None)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Missing bearer token"

    with pytest.raises(HTTPException) as exc_info:
        current_user("")
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Missing bearer token"


def test_non_bearer_scheme_raises_missing_token():
    for authorization in ("Token owner-token", "bearer owner-token", "owner-token"):
        with pytest.raises(HTTPException) as exc_info:
            current_user(authorization)
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Missing bearer token"


def test_unknown_token_raises_invalid_token():
    # "Bearer " clears the scheme check and leaves an empty token, so it fails the lookup
    # rather than the prefix check and must report "Invalid token".
    for authorization in ("Bearer unknown-token", "Bearer "):
        with pytest.raises(HTTPException) as exc_info:
            current_user(authorization)
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Invalid token"


def test_valid_tokens_resolve_to_user_records():
    assert current_user(OWNER) == {"id": 1, "role": "owner"}
    assert current_user(MEMBER) == {"id": 2, "role": "member"}
