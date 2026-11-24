from fastapi.testclient import TestClient

import app.main as app_main
from app.main import app, PROJECTS, TASKS, USAGE, seed_sessions

client = TestClient(app)
OWNER = {"Authorization": "Bearer owner-token"}
MEMBER = {"Authorization": "Bearer member-token"}


def setup_function():
    PROJECTS.clear()
    TASKS.clear()
    USAGE["used"] = 0
    app_main.TEAM.clear()
    app_main.TEAM.update({1: "owner", 2: "member"})
    app_main.NEXT_PROJECT = 1
    app_main.NEXT_TASK = 1
    seed_sessions()


def test_owner_token_authenticates_within_lifetime():
    assert client.get("/projects", headers=OWNER).status_code == 200
    created = client.post("/projects", json={"name": "Lifecycle QA"}, headers=OWNER)
    assert created.status_code == 200
    assert created.json()["owner_id"] == 1


def test_token_is_accepted_immediately_before_expiry(monkeypatch):
    expires_at = app_main.SESSIONS["owner-token"]["expires_at"]
    # The guard rejects at monotonic() >= expires_at, so the last instant it accepts
    # is strictly before expiry: one epsilon below. The boundary instant itself is
    # covered by test_token_is_rejected_at_expiry_instant.
    monkeypatch.setattr(app_main, "monotonic", lambda: expires_at - 1e-6)
    assert client.get("/projects", headers=OWNER).status_code == 200


def test_token_is_rejected_at_expiry_instant(monkeypatch):
    expires_at = app_main.SESSIONS["owner-token"]["expires_at"]
    monkeypatch.setattr(app_main, "monotonic", lambda: expires_at)
    rejected = client.get("/projects", headers=OWNER)
    assert rejected.status_code == 401
    assert rejected.json() == {"detail": "Invalid token"}
    assert "owner-token" not in app_main.SESSIONS


def test_expired_token_stops_working_on_every_guarded_route(monkeypatch):
    record = app_main.SESSIONS["owner-token"]
    monkeypatch.setattr(app_main, "monotonic", lambda: record["expires_at"])

    # Rejecting an expired credential also deletes its record, so restore it before
    # every call: each route must face a session that is present but past its window.
    guarded_routes = [
        ("post", "/projects", {"json": {"name": "Expired Write"}}),
        ("post", "/tasks", {"json": {"title": "Expired Task", "project_id": 1}}),
        ("patch", "/tasks/1", {"params": {"status": "done"}}),
        ("post", "/teams/1/role", {"params": {"role": "member"}}),
        ("post", "/billing/consume", {"params": {"units": 1}}),
    ]
    for method, url, kwargs in guarded_routes:
        app_main.SESSIONS["owner-token"] = record
        rejected = getattr(client, method)(url, headers=OWNER, **kwargs)
        assert rejected.status_code == 401
        assert rejected.json() == {"detail": "Invalid token"}
        assert "owner-token" not in app_main.SESSIONS


def test_revoked_token_is_rejected():
    revoked = client.post("/sessions/revoke", headers=OWNER)
    assert revoked.status_code == 200
    assert revoked.json() == {"revoked": True}
    rejected = client.get("/projects", headers=OWNER)
    assert rejected.status_code == 401
    assert rejected.json() == {"detail": "Invalid token"}
    assert "owner-token" not in app_main.SESSIONS

    # Termination is per-credential, so the other principal's session must survive.
    assert client.get("/projects", headers=MEMBER).status_code == 200


def test_rotation_issues_new_token_and_retires_old():
    rotated = client.post("/sessions/rotate", headers=OWNER)
    assert rotated.status_code == 200
    assert rotated.headers["Cache-Control"] == "no-store"
    body = rotated.json()
    assert body["token_ttl_seconds"] == 900
    assert body["token"] != "owner-token"
    assert body["token"] in app_main.SESSIONS

    # The route echoes the constant; the guard enforces the span it recorded.
    issued = app_main.SESSIONS[body["token"]]
    assert abs((issued["expires_at"] - issued["issued_at"]) - 900) < 1e-6

    assert client.get("/projects", headers=OWNER).status_code == 401
    replacement = {"Authorization": f"Bearer {body['token']}"}
    assert client.get("/projects", headers=replacement).status_code == 200
    # The owner role has to survive rotation, so an owner-only route still passes.
    reassigned = client.post(
        "/teams/2/role", params={"role": "member"}, headers=replacement
    )
    assert reassigned.status_code == 200

    # A replacement must carry the presented session's identity and role, not the
    # owner's.
    member_rotated = client.post("/sessions/rotate", headers=MEMBER)
    assert member_rotated.status_code == 200
    member_token = {"Authorization": f"Bearer {member_rotated.json()['token']}"}
    member_forbidden = client.post(
        "/teams/2/role", params={"role": "member"}, headers=member_token
    )
    assert member_forbidden.status_code == 403
    assert member_forbidden.json() == {"detail": "Owner role required"}
    member_project = client.post(
        "/projects", json={"name": "Member Project"}, headers=member_token
    )
    assert member_project.status_code == 200
    assert member_project.json()["owner_id"] == 2


def test_unknown_and_malformed_headers_are_rejected(monkeypatch):
    malformed = [
        None,
        {"Authorization": "Bearer"},
        {"Authorization": "Basic abc"},
        {"Authorization": "bearer owner-token"},
    ]
    for headers in malformed:
        rejected = client.get("/projects", headers=headers)
        assert rejected.status_code == 401
        assert rejected.json() == {"detail": "Missing bearer token"}

    # The in-process transport delivers the header value to the guard unparsed, so
    # the empty-token and whitespace assertions below cover the guard's own
    # geometry; an HTTP/1.1 server strips the optional trailing whitespace before
    # the application sees the header, which would reject "Bearer " as a missing
    # prefix rather than an invalid token.
    empty_token = client.get("/projects", headers={"Authorization": "Bearer "})
    assert empty_token.status_code == 401
    assert empty_token.json() == {"detail": "Invalid token"}

    double_space = client.get(
        "/projects", headers={"Authorization": "Bearer  owner-token"}
    )
    assert double_space.status_code == 401
    assert double_space.json() == {"detail": "Invalid token"}

    trailing_space = client.get(
        "/projects", headers={"Authorization": "Bearer owner-token "}
    )
    assert trailing_space.status_code == 401
    assert trailing_space.json() == {"detail": "Invalid token"}

    unknown = client.get("/projects", headers={"Authorization": "Bearer nope"})
    assert unknown.status_code == 401
    assert unknown.json() == {"detail": "Invalid token"}

    assert client.post("/sessions/rotate", headers=OWNER).status_code == 200
    retired = client.get("/projects", headers=OWNER)
    assert retired.status_code == 401
    assert retired.json() == {"detail": "Invalid token"}

    # Patch the clock only after the rotation above: an expired credential
    # cannot rotate, so the expired variant would block it.
    expires_at = app_main.SESSIONS["member-token"]["expires_at"]
    monkeypatch.setattr(app_main, "monotonic", lambda: expires_at)
    expired = client.get("/projects", headers=MEMBER)
    assert expired.status_code == 401
    assert expired.json() == {"detail": "Invalid token"}


def test_unauthenticated_routes_and_role_checks_unchanged():
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200
    assert client.get("/metrics").status_code == 200

    forbidden = client.post("/teams/2/role", params={"role": "member"}, headers=MEMBER)
    assert forbidden.status_code == 403
    assert forbidden.json() == {"detail": "Owner role required"}

    missing_task = client.patch("/tasks/999", params={"status": "done"}, headers=OWNER)
    assert missing_task.status_code == 404
    assert missing_task.json() == {"detail": "Task not found"}

    invalid_units = client.post("/billing/consume", params={"units": 0}, headers=OWNER)
    assert invalid_units.status_code == 422
    assert invalid_units.json() == {"detail": "Units must be positive"}
