import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
OWNER = {"Authorization": "Bearer owner-token"}
MEMBER = {"Authorization": "Bearer member-token"}


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Role grants are written to TEAM but authorization reads USERS, so a promoted "
        "member keeps member rights (app/main.py:29, app/main.py:58, app/main.py:89-90 "
        "vs app/main.py:93)"
    ),
)
def test_role_grant_governs_later_requests():
    owner_project = client.post("/projects", json={"name": "Owner Scope"}, headers=OWNER)
    assert owner_project.status_code == 200
    owner_project_id = owner_project.json()["id"]

    member_project = client.post("/projects", json={"name": "Member Scope"}, headers=MEMBER)
    assert member_project.status_code == 200
    member_project_id = member_project.json()["id"]

    before_grant = client.get("/projects", headers=MEMBER)
    assert before_grant.status_code == 200
    assert [p["id"] for p in before_grant.json()] == [member_project_id]

    grant = client.post("/teams/2/role", params={"role": "owner"}, headers=OWNER)
    assert grant.status_code == 200
    assert grant.json() == {"user_id": 2, "role": "owner"}

    # This set comparison is the assertion the application does not satisfy: the grant
    # is stored in TEAM, while authorization keeps reading the seeded USERS role.
    after_grant = client.get("/projects", headers=MEMBER)
    assert after_grant.status_code == 200
    assert {p["id"] for p in after_grant.json()} == {owner_project_id, member_project_id}

    revoke = client.post("/teams/1/role", params={"role": "member"}, headers=MEMBER)
    assert revoke.status_code == 200
    assert revoke.json() == {"user_id": 1, "role": "member"}

    after_revoke = client.get("/projects", headers=OWNER)
    assert after_revoke.status_code == 200
    assert [p["id"] for p in after_revoke.json()] == [owner_project_id]


def test_owner_lists_every_project_and_member_lists_only_its_own():
    owner_project = client.post("/projects", json={"name": "Owner Scope"}, headers=OWNER)
    assert owner_project.status_code == 200
    assert owner_project.json()["owner_id"] == 1
    owner_project_id = owner_project.json()["id"]

    member_project = client.post("/projects", json={"name": "Member Scope"}, headers=MEMBER)
    assert member_project.status_code == 200
    assert member_project.json()["owner_id"] == 2
    member_project_id = member_project.json()["id"]

    # The two listings below bind both branches of the project-visibility filter in
    # app/main.py:58 outside the expected-failure marker above: a filter that shows a
    # member every project, or one that hides a teammate's project from an owner,
    # fails this test.
    member_listing = client.get("/projects", headers=MEMBER)
    assert member_listing.status_code == 200
    assert [p["id"] for p in member_listing.json()] == [member_project_id]

    owner_listing = client.get("/projects", headers=OWNER)
    assert owner_listing.status_code == 200
    assert {p["id"] for p in owner_listing.json()} == {owner_project_id, member_project_id}


def test_member_cannot_change_roles():
    response = client.post("/teams/1/role", params={"role": "member"}, headers=MEMBER)
    assert response.status_code == 403
    assert response.json()["detail"] == "Owner role required"


def test_role_change_rejects_unknown_user_and_unknown_role():
    unknown_user = client.post("/teams/99/role", params={"role": "owner"}, headers=OWNER)
    assert unknown_user.status_code == 404
    assert unknown_user.json()["detail"] == "User or role not found"

    unknown_role = client.post("/teams/2/role", params={"role": "admin"}, headers=OWNER)
    assert unknown_role.status_code == 404
    assert unknown_role.json()["detail"] == "User or role not found"
