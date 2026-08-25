# Klaros AI

The AI operating system for the one-person company. Klaros connects the systems a business
already uses, understands events and business context, recommends/decides, executes approved
actions through controlled tools, watches ongoing workflows, and escalates to the owner.

This is a real, incrementally-built multi-tenant SaaS platform — not a mock. See
`PROJECT_STATUS.md` for exactly what is functional today versus planned.

## Architecture

```
Frontend (Next.js/TS/Tailwind) → Backend API (FastAPI/Pydantic, async)
                                       ↓
                          PostgreSQL (+pgvector) · Redis · Temporal
```

- **Multi-tenancy**: every tenant-owned row carries `tenant_id`; enforced at the query layer via
  the authenticated JWT, never from client input.
- **RBAC**: fixed role set (`OWNER, ADMIN, MANAGER, STAFF, TECHNICIAN, ACCOUNTANT, READ_ONLY`)
  mapped to a permission matrix in `backend/app/models/rbac.py`.
- **Audit log**: `audit_logs` table records every important action (human or AI), currently
  written on organization creation; the AI orchestration and tool layers (Phase 2+) will write to
  it on every tool execution.

## Local setup

Prereqs: Python 3.12, Node 20, Docker (for Postgres/Redis/Temporal), or use `uv` to get Python
3.12 without touching your system Python:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Backend

```bash
cd backend
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -r requirements.txt
cp ../.env.example ../.env   # then fill in DATABASE_URL etc.
alembic upgrade head
uvicorn app.main:app --reload
```

API docs: http://localhost:8000/docs

### Frontend

```bash
cd frontend
npm install
npm run dev
```

App: http://localhost:3000

### Full stack via Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

Brings up Postgres (with pgvector), Redis, Temporal + Temporal UI (http://localhost:8080),
backend (http://localhost:8000), worker, and frontend (http://localhost:3000).

## Running tests

```bash
cd backend
source .venv/bin/activate
python -m pytest -q
```

Tests run against an in-memory SQLite database (no external services required) and currently
cover registration, login, and the tenant-isolation critical scenario (Tenant A cannot read
Tenant B's data).

## Database migrations

Alembic is configured (`backend/alembic/`). The initial migration (`0001_initial_schema`) creates
`organizations`, `users`, and `audit_logs`. Do not hand-edit the schema — add a new migration:

```bash
alembic revision --autogenerate -m "description"
alembic upgrade head
```

## Environment variables

See `.env.example`. Third-party integration credentials (QuickBooks, Stripe, Google Ads, etc.)
are listed but intentionally left blank — those integrations are not yet implemented (see
`PROJECT_STATUS.md`). The app must never claim a disconnected integration is working.

## Troubleshooting

- **`str | None` / `TypeError` on startup**: you're on Python < 3.10. Use `uv venv --python 3.12`.
- **`email-validator is not installed`**: `pip install pydantic[email]` (already pinned in
  `requirements.txt`).
- **bcrypt `password cannot be longer than 72 bytes` / passlib crash**: caused by
  `passlib==1.7.4` + `bcrypt>=4.1`. This repo pins `bcrypt==4.0.1`, which is compatible.
