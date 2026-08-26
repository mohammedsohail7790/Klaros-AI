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
                    Event Bus (Postgres store + Redis Streams transport)
                                       ↓
                    Temporal (workflows) ──→ Tool Registry ──→ AuditLog
                                       ↓
                          PostgreSQL (+pgvector) · Redis
```

- **Multi-tenancy**: every tenant-owned row carries `tenant_id`; enforced at the query layer via
  the authenticated JWT, never from client input.
- **RBAC**: fixed role set (`OWNER, ADMIN, MANAGER, STAFF, TECHNICIAN, ACCOUNTANT, READ_ONLY`)
  mapped to a permission matrix in `backend/app/models/rbac.py`.
- **Audit log**: `audit_logs` table records every important action (human, AI, system, or
  workflow) — written on org creation (Phase 1) and on every tool execution (Phase 2, see below).

### Event architecture (Phase 2)

`backend/app/events/`. Postgres (`events` table) is the durable source of truth — `publish()`
writes there before ever touching the transport, so nothing is lost if Redis is down. The
transport (`EventTransport`, `backend/app/events/transport.py`) is a thin abstraction over Redis
Streams + consumer groups (`RedisStreamTransport`) so moving to Kafka/NATS later means writing one
new class, not touching the bus or any handler. An `InMemoryTransport` implementing the identical
interface backs tests and an opt-in dev fallback (`EVENT_TRANSPORT=memory`) when no Redis is
configured — not a mock, a real in-process transport with the same delivery semantics.

Duplicate delivery is handled at two independent levels:
1. **Publish-time**: a `(tenant_id, idempotency_key)` unique constraint — republishing the same
   key returns the original event instead of creating a second one.
2. **Handler-level**: an `event_processing_records` row per `(event_id, handler_name)` — even if
   the same event is delivered to a handler twice, the handler body only runs once.

Failed handlers retry (configurable `max_retries`, default 3) then move to `dead_letter_events`;
`EventBus.replay()` resets and reruns a dead-lettered (event, handler) pair.

### Tool / MCP framework (Phase 2)

`backend/app/tools/`. Every capability — human, AI, or workflow — goes through `ToolRegistry`,
which is the single enforcement point:

```
lookup → permission check → tenant check → schema validation (Pydantic)
       → policy check (AUTO / APPROVAL_REQUIRED / BLOCKED) → execute() → audit
```

There is no other path to a tool's side effect. `APPROVAL_REQUIRED` tools never run — they create
an `ApprovalRequest` row and return; only a human `POST /api/v1/approvals/{id}/approve` unblocks
the underlying action (which itself must be re-invoked — Phase 2 does not auto-resume). `BLOCKED`
tools never run at all. The default policy table lives in `backend/app/tools/policy.py`.

Built-in tools: `system.get_tenant_context`, `system.get_current_time`, `events.publish_event`,
`events.get_event`, `approvals.create_request`, `notifications.create_notification`,
`audit.record_action`.

**AI execution boundary**: `backend/app/ai/execution_service.py`'s `AIExecutionService` is the
only surface Phase 7's AI orchestration will be allowed to call — it accepts a structured
`ToolRequest` (tool name + input dict) and hands it straight to `ToolRegistry`. There is no method
here that accepts SQL, a shell command, an HTTP URL, or a Python callable.

### Temporal (Phase 2)

`backend/app/workflows/`. `EventProcessingWorkflow` is the generic shape: receive an event id +
tool request, run `execute_tool_activity` (which goes through the same `ToolRegistry`), return the
result. `InvoiceOverdueWorkflow` is the spec's demo long-running workflow: wait → send reminder →
wait → check payment → escalate (create a notification) if still unpaid. `send_reminder_activity`
and `check_payment_status_activity` are explicitly labeled **internal test actions** in their own
docstrings/log lines — there is no Finance module or Communication provider yet, so they do not
call Stripe/QuickBooks/Twilio. See "Known limitations" below for what could and couldn't be
verified against a real Temporal server in this environment.

### Integration framework (Phase 2)

`backend/app/integrations/`. Provider interfaces (`FinanceProvider`, `CRMProvider`,
`OperationsProvider`, `MarketingProvider`, `CommunicationProvider`, `CalendarProvider`) and
placeholder adapters (QuickBooks, Stripe, ServiceTitan, Jobber, Google Ads, Meta Ads, Gmail).
`GET /api/v1/integrations` reports each adapter's real status — `NOT_CONNECTED` whenever its
credential env vars are unset, which is always true today since none are configured. No adapter
fabricates an API response.

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

Tests run against an in-memory SQLite database (no external services required) and cover:
registration/login, tenant isolation, event publish/subscribe/idempotency/retry/dead-letter/replay,
tool registry authorization (permission/tenant/schema/policy), the approval boundary (AUTO /
APPROVAL_REQUIRED / BLOCKED), and full end-to-end acceptance scenarios (happy path, unauthorized
tool, duplicate event) per `PROJECT_STATUS.md`.

Temporal workflow tests (`tests/test_temporal_workflows.py`) are separate: they spin up
temporalio's ephemeral local test server (a real standalone binary, downloaded over the network —
no Docker needed) rather than running against sqlite. If that download or binary can't run in your
environment, those tests skip themselves with the reason rather than being silently omitted.

## Database migrations

Alembic is configured (`backend/alembic/`). `0001_initial_schema` creates `organizations`,
`users`, and `audit_logs`. `0002_event_bus_tools_approvals` creates `events`,
`event_processing_records`, `dead_letter_events`, `approval_requests`, and `notifications`. Do not
hand-edit the schema — add a new migration:

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
