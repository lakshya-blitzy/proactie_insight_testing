import pytest
from fastapi import HTTPException

import app.main as main
from app.main import consume_usage

OWNER = "Bearer owner-token"


def test_consume_usage_requires_authorization():
    # units=1 is a valid quantity, so only the bearer-token guard can reject this call.
    with pytest.raises(HTTPException) as missing:
        consume_usage(1, None)
    assert missing.value.status_code == 401
    assert missing.value.detail == "Missing bearer token"

    # The guard runs before the quota arithmetic, so the refusal consumed nothing.
    assert main.USAGE["used"] == 0


def test_consume_usage_rejects_non_positive_units():
    with pytest.raises(HTTPException) as zero:
        consume_usage(0, OWNER)
    assert zero.value.status_code == 422
    assert zero.value.detail == "Units must be positive"

    with pytest.raises(HTTPException) as negative:
        consume_usage(-1, OWNER)
    assert negative.value.status_code == 422
    assert negative.value.detail == "Units must be positive"

    assert main.USAGE["used"] == 0


def test_consume_usage_accumulates_to_limit():
    # consume_usage returns the live USAGE dict rather than a copy, so each result is
    # compared at the moment of its call; a held dict would be mutated by the next call.
    assert consume_usage(2, OWNER) == {"used": 2, "limit": 5}
    assert main.USAGE == {"used": 2, "limit": 5}

    # used + units == limit is the allowed boundary, not an off-by-one refusal.
    assert consume_usage(3, OWNER) == {"used": 5, "limit": 5}
    assert main.USAGE == {"used": 5, "limit": 5}


def test_consume_usage_rejects_beyond_limit():
    consume_usage(5, OWNER)
    assert main.USAGE == {"used": 5, "limit": 5}

    with pytest.raises(HTTPException) as over:
        consume_usage(1, OWNER)
    assert over.value.status_code == 429
    assert over.value.detail == "Usage limit reached"

    assert main.USAGE["used"] == 5
    assert main.USAGE["limit"] == 5
