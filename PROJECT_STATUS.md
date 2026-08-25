# Klaros AI — Project Status

Last updated: 2026-08-26 (Phase 1 checkpoint)

## What's actually built and verified

- **Multi-tenant data model**: `organizations`, `users`, `audit_logs` with `tenant_id` on every
  tenant-owned row (`backend/app/db/base.py`, `backend/app/models/`).
- **Auth**: registration (creates an org + OWNER user), login, JWT access/refresh tokens with
  bcrypt password hashing (`backend/app/services/auth_service.py`,
  `backend/app/api/v1/auth.py`).
- **RBAC**: 7-role permission matrix (`backend/app/models/rbac.py`), enforced via
  `require_permission()` FastAPI dependency (`backend/app/api/deps.py`) — not yet applied to any
  protected business endpoint because no business endpoints exist yet beyond `/users/me`.
- **Tenant isolation enforcement**: every query filters by `tenant_id` taken from the decoded JWT,
  never from client-supplied input.
- **Audit logging**: `organization.created` is recorded on signup as the first real audit trail
  entry.
- **Structured logging**: JSON logs via `structlog`.
- **Alembic migrations**: `0001_initial_schema` creates the schema (written by hand since no
  local/Docker Postgres was available in the dev sandbox to run `--autogenerate`; verify it
  applies cleanly against real Postgres before relying on it — see Known Limitations).
- **Tests, actually run and passing** (5/5, `backend/tests/`):
  - register + login succeed
  - wrong password rejected (401)
  - **Tenant A cannot read Tenant B's data** — the critical multi-tenancy scenario from the spec
  - missing/invalid token rejected (401)
- **End-to-end live verification**: booted the real FastAPI server (not just the test client) and
  hit `/api/v1/auth/register`, `/auth/login`, and `/users/me` with `curl` — confirmed real HTTP
  round trips, not just unit tests.
- **Frontend**: Next.js 14 + TypeScript + Tailwind — landing, login, register, and a dashboard
  shell that reads the real authenticated user from the API. The dashboard explicitly labels every
  unbuilt module ("Today", "Approvals", "AI Handled", etc.) as **"not connected"** rather than
  showing fabricated numbers, per the no-fake-demo rule.
- **Docker Compose**: postgres (pgvector image), redis, temporal + temporal-ui, backend, worker,
  frontend, all wired with healthchecks and env vars.

## What is explicitly stubbed / not yet built

Everything past Phase 1 in the spec's phase sequence is **not implemented**:

- Event bus, event store, idempotency/dedup, dead-letter handling
- Temporal workflow engine integration (the `worker` container currently just idles — see
  `backend/app/workers/main.py`)
- MCP tool layer / typed AI tools / LangGraph orchestration
- Approval engine
- Leads, customers, jobs, scheduling, quotes, invoices, payments — no domain models yet beyond
  auth/org
- All third-party integrations (QuickBooks, Xero, Stripe, ServiceTitan, Jobber, Google Ads, Meta
  Ads, Google Business, Twilio, SendGrid, etc.) — zero adapters written yet. `.env.example` lists
  the credential slots but nothing reads them.
- Knowledge base / pgvector embeddings / semantic search
- Exception engine, notifications, reporting, global search, file/document pipeline
- Billing/plan enforcement (the `plan` and `billing_status` columns exist on `organizations` but
  nothing acts on them)
- Autonomy levels are modeled as a column (`autonomy_level`, defaults to `LEVEL_0`) but nothing
  reads it yet — no AI actions exist to gate

## Known limitations / caveats

- **No Postgres or Docker available in this dev sandbox.** All verification above ran against an
  in-memory SQLite database via a generic SQLAlchemy `Uuid` type (not Postgres-native `UUID`) so
  tests can run without external services. The Alembic migration and Docker Compose Postgres
  service are written correctly for Postgres but have **not been executed against a real Postgres
  instance** — run `docker compose up` and `alembic upgrade head` yourself as the first
  verification step before building further on top of them.
- **No Node/npm available in this dev sandbox.** The frontend code was written carefully but
  **not run or visually verified in a browser.** Run `npm install && npm run dev` and check it
  before trusting it end-to-end.
- Refresh-token rotation/revocation is not implemented — refresh tokens are issued but there's no
  endpoint to redeem them yet.
- `EXECUTE_AI_ACTION` permission exists in the matrix but there is no AI action to gate — it's a
  placeholder for Phase 7.

## Recommended next step

Phase 2 (event bus + workflow engine + MCP tool framework + audit-on-every-tool-call) is the next
increment, since almost everything else in the spec (leads, jobs, finance, marketing) depends on
events flowing through that layer rather than being bolted directly onto CRUD endpoints.
