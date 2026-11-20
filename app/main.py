from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from time import monotonic

app = FastAPI(title="Proactive Insights Demo API", version="1.0.0")

USERS = {"owner-token": {"id": 1, "role": "owner"}, "member-token": {"id": 2, "role": "member"}}
PROJECTS = {}
TASKS = {}
TEAM = {1: "owner", 2: "member"}
USAGE = {"used": 0, "limit": 5}
NEXT_PROJECT = 1
NEXT_TASK = 1


class ProjectIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)


class TaskIn(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    project_id: int


def current_user(authorization: str | None):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = authorization.removeprefix("Bearer ")
    user = USERS.get(token)
    if not user:
        raise HTTPException(401, "Invalid token")
    return user


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
    return {"requests_total": 0, "uptime_seconds": round(monotonic(), 2)}
