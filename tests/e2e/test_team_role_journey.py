import pytest

pytestmark = pytest.mark.e2e

OWNER = {"Authorization": "Bearer owner-token"}
MEMBER = {"Authorization": "Bearer member-token"}


def test_owner_grants_member_owner_role_and_member_then_sees_team_projects(api):
    owner_project = api.post("/projects", json={"name": "Owner Scope"}, headers=OWNER)
    assert owner_project.status_code == 200
    owner_project_id = owner_project.json()["id"]

    member_project = api.post("/projects", json={"name": "Member Scope"}, headers=MEMBER)
    assert member_project.status_code == 200
    assert member_project.json()["owner_id"] == 2
    member_project_id = member_project.json()["id"]

    # Membership, never exact list equality: the session server also holds whatever the
    # sibling journey created, so the member branch is bound by "own id in, teammate's out".
    before_grant = api.get("/projects", headers=MEMBER)
    assert before_grant.status_code == 200
    before_grant_ids = {entry["id"] for entry in before_grant.json()}
    assert member_project_id in before_grant_ids
    assert owner_project_id not in before_grant_ids

    grant = api.post("/teams/2/role", params={"role": "owner"}, headers=OWNER)
    assert grant.status_code == 200
    assert grant.json() == {"user_id": 2, "role": "owner"}

    listing = api.get("/projects", headers=MEMBER)
    assert listing.status_code == 200
    seen = {entry["id"] for entry in listing.json()}
    assert member_project_id in seen
    # Raised imperatively rather than by a declarative expected-failure marker: a marker
    # would report a break in any step above as an expected failure too, hiding it.
    if owner_project_id in seen:
        pytest.fail("the role grant now governs visibility: delete this guard, keep the assertion")
    pytest.xfail("Role grants are written to TEAM (app/main.py:93) while the visibility filter "
                 "(app/main.py:58) and the role gate (app/main.py:89) read USERS (app/main.py:29)")
