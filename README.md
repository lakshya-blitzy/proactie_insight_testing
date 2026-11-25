# Proactive Insights Demo API

A compact FastAPI service designed as a test fixture for repository-level proactive insights.

## What is included
- A small project/task API with authentication-like bearer tokens that expire and can be rotated or revoked, plus role checks.
- Project and task workflows, team membership, and a simulated billing limit.
- A deliberately small test suite: it covers a few happy paths while leaving important workflows and non-functional behaviours untested.
- No external database or service is required; data is held in memory.

## Run locally
```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000/docs.

## Authentication
The two demo tokens (`owner-token` for the owner, `member-token` for the member) are seeded when the process starts, and each one is valid for 900 seconds from that moment rather than indefinitely. The window is a fixed value in `app/main.py`, not an environment setting, so the fixture stays self-contained.

A token that has expired, been revoked or been rotated is rejected with `401 Invalid token` on every guarded route, and the response does not say which of the three happened. Both routes below need a valid bearer token, so a lapsed credential cannot be renewed in-request: recovery is a restart of the process, which re-seeds the two demo tokens with a fresh 900-second window.

- `POST /sessions/rotate` retires the presented token and returns a replacement for the same identity and role in a `token` field, with `token_ttl_seconds` giving the new window and `Cache-Control: no-store` so an intermediary does not cache the credential.
- `POST /sessions/revoke` ends the caller's own session and returns `{"revoked": true}`.

## Run tests
```bash
python -m pytest -q
```
The `-m` form is required because `tests/` is not a package, so only `python -m pytest` puts the repository root on the import path; the bare `pytest -q` command fails with `ModuleNotFoundError: No module named 'app'`.

## Suggested scan setup
- Scan the repository root.
- Select the `main` branch.
- Do not configure an environment; this fixture is self-contained.

## Important
This is a synthetic QA fixture, not a production application. Its in-memory storage, demo tokens, and intentionally incomplete test coverage are for scanner evaluation only. Do not deploy it or use real credentials.
