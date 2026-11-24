from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from time import monotonic
import secrets

app = FastAPI(title="Proactive Insights Demo API", version="1.0.0")

USERS = {"owner-token": {"id": 1, "role": "owner"}, "member-token": {"id": 2, "role": "member"}}
PROJECTS = {}
TASKS = {}
TEAM = {1: "owner", 2: "member"}
USAGE = {"used": 0, "limit": 5}
NEXT_PROJECT = 1
NEXT_TASK = 1
TOKEN_TTL_SECONDS = 900
SESSIONS: dict[str, dict] = {}
STARTED_AT = monotonic()
REQUESTS_TOTAL = 0


class ProjectIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)


class TaskIn(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    project_id: int


def _issue_session(user: dict, token: str | None = None) -> str:
    """Record a session in SESSIONS and return its credential."""
    if token is None:
        token = secrets.token_urlsafe(32)
    now = monotonic()
    # USERS is only the seed directory of record, copied in by seed_sessions();
    # validity is this record's expires_at, never membership in USERS.
    SESSIONS[token] = {"id": user["id"], "role": user["role"],
                       "issued_at": now, "expires_at": now + TOKEN_TTL_SECONDS}
    return token


def seed_sessions() -> None:
    SESSIONS.clear()
    for token, user in USERS.items():
        _issue_session(user, token)


def _resolve_session(authorization: str | None):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = authorization.removeprefix("Bearer ")
    session = SESSIONS.get(token)
    if session is None:
        raise HTTPException(401, "Invalid token")
    if monotonic() >= session["expires_at"]:
        del SESSIONS[token]
        raise HTTPException(401, "Invalid token")
    return token, session


def current_user(authorization: str | None):
    _, session = _resolve_session(authorization)
    return {"id": session["id"], "role": session["role"]}


seed_sessions()


@app.middleware("http")
async def count_requests(request: Request, call_next):
    global REQUESTS_TOTAL
    REQUESTS_TOTAL += 1
    return await call_next(request)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def readiness():
    return {"ready": True, "checks": {"storage": "ok"}}


@app.post("/projects")
def create_project(payload: ProjectIn, authorization: str | None = Header(default=None)):
    global NEXT_PROJECT
    user = current_user(authorization)
    project = {"id": NEXT_PROJECT, "name": payload.name, "owner_id": user["id"]}
    PROJECTS[NEXT_PROJECT] = project
    NEXT_PROJECT += 1
    return project


@app.get("/projects")
def list_projects(authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    return [p for p in PROJECTS.values() if p["owner_id"] == user["id"] or user["role"] == "owner"]


@app.post("/tasks")
def create_task(payload: TaskIn, authorization: str | None = Header(default=None)):
    global NEXT_TASK
    user = current_user(authorization)
    if payload.project_id not in PROJECTS:
        raise HTTPException(404, "Project not found")
    task = {"id": NEXT_TASK, "title": payload.title, "project_id": payload.project_id,
            "created_by": user["id"], "status": "open"}
    TASKS[NEXT_TASK] = task
    NEXT_TASK += 1
    return task


@app.patch("/tasks/{task_id}")
def update_task(task_id: int, status: str, authorization: str | None = Header(default=None)):
    current_user(authorization)
    task = TASKS.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if status not in {"open", "in_progress", "done"}:
        raise HTTPException(422, "Unsupported status")
    task["status"] = status
    return task


@app.post("/teams/{user_id}/role")
def change_role(user_id: int, role: str, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    if user["role"] != "owner":
        raise HTTPException(403, "Owner role required")
    if user_id not in TEAM or role not in {"owner", "member"}:
        raise HTTPException(404, "User or role not found")
    TEAM[user_id] = role
    return {"user_id": user_id, "role": role}


@app.post("/billing/consume")
def consume_usage(units: int = 1, authorization: str | None = Header(default=None)):
    current_user(authorization)
    if units < 1:
        raise HTTPException(422, "Units must be positive")
    if USAGE["used"] + units > USAGE["limit"]:
        raise HTTPException(429, "Usage limit reached")
    USAGE["used"] += units
    return USAGE


@app.get("/metrics")
def metrics():
    return {"requests_total": REQUESTS_TOTAL,
            "uptime_seconds": round(monotonic() - STARTED_AT, 2)}


@app.post("/sessions/rotate")
def rotate_session(response: Response, authorization: str | None = Header(default=None)):
    token, session = _resolve_session(authorization)
    del SESSIONS[token]
    new_token = _issue_session(session)
    response.headers["Cache-Control"] = "no-store"
    return {"token": new_token, "token_ttl_seconds": TOKEN_TTL_SECONDS}


@app.post("/sessions/revoke")
def revoke_session(authorization: str | None = Header(default=None)):
    token, _ = _resolve_session(authorization)
    del SESSIONS[token]
    return {"revoked": True}
