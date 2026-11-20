# Proactive Insights Demo API

A compact FastAPI service designed as a test fixture for repository-level proactive insights.

## What is included
- A small project/task API with authentication-like bearer tokens and role checks.
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

## Run tests
```bash
pytest -q
```

## Suggested scan setup
- Scan the repository root.
- Select the `main` branch.
- Do not configure an environment; this fixture is self-contained.

## Important
This is a synthetic QA fixture, not a production application. Its in-memory storage, demo tokens, and intentionally incomplete test coverage are for scanner evaluation only. Do not deploy it or use real credentials.
