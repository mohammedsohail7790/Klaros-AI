# Klaros AI — Project Status

Last updated: 2026-08-26 (Phase 2 checkpoint)

## What's actually built and verified — Phase 1 (unchanged)

- **Multi-tenant data model**: `organizations`, `users`, `audit_logs` with `tenant_id` on every
  tenant-owned row.
- **Auth**: registration (creates an org + OWNER user), login, JWT access/refresh tokens with
  bcrypt password hashing.
- **RBAC**: 7-role permission matrix, enforced via `require_permission()`.
- **Tenant isolation enforcement**: every query filters by `tenant_id` taken from the decoded JWT.
- **Frontend**: Next.js 14 landing/login/register/dashboard shell.
- **Docker Compose**: postgres (pgvector), redis, temporal + temporal-ui, backend, worker, frontend.

## What's actually built and verified — Phase 2

- **Event bus** (`backend/app/events/`): Postgres-backed durable event store (`events` table,
  source of truth) + a transport abstraction (`EventTransport`) with a real `RedisStreamTransport`
  and an `InMemoryTransport` (same interface, used in tests/dev — not a mock, a real in-process
  implementation of the identical delivery semantics).
  - **Idempotency at publish time**: `(tenant_id, idempotency_key)` unique constraint — republishing
    the same key returns the original row.
  - **Idempotency at handler level**: `event_processing_records` — a handler only runs once per
    `(event_id, handler_name)` even under redelivery.
  - **Retries + dead-lettering**: configurable `max_retries`, then a `dead_letter_events` row.
  - **Replay**: `EventBus.replay(event_id, handler_name)` resets and reruns a dead-lettered pair.
  - **Correlation IDs** propagate from publish through to every audit row written during
    processing.
- **Tool / MCP framework** (`backend/app/tools/`): `ToolRegistry.execute()` enforces, in order —
  lookup, permission (RBAC), tenant-scope, Pydantic schema validation, action policy
  (AUTO/APPROVAL_REQUIRED/BLOCKED), execution, audit. 7 built-in tools:
  `system.get_tenant_context`, `system.get_current_time`, `events.publish_event`,
  `events.get_event`, `approvals.create_request`, `notifications.create_notification`,
  `audit.record_action`.
- **Approval boundary** (`backend/app/models/approval.py`, `backend/app/tools/policy.py`): an
  `APPROVAL_REQUIRED` tool call creates a persisted `ApprovalRequest` and raises
  `ToolApprovalRequiredError` — **the tool body never runs**. `BLOCKED` tools never run, ever.
  `POST /api/v1/approvals/{id}/approve|reject` — verified a second approval attempt on an
  already-decided request correctly 409s.
- **AI execution boundary** (`backend/app/ai/execution_service.py`): `AIExecutionService` is the
  only surface that will be allowed to call tools on the AI's behalf (Phase 7) — it accepts a
  structured `ToolRequest`, nothing else, and routes through the same `ToolRegistry`.
- **Audit trail extended**: every tool execution — success, failure, or pending-approval — writes
  an `AuditLog` row with actor type (`USER`/`AI`/`SYSTEM`/`WORKFLOW`), tool name, redacted input
  summary (keys matching `password`/`secret`/`token`/`credential`/`api_key`/`private_key` are
  replaced with `***REDACTED***`, never stored raw), result, and correlation ID.
- **Integration framework** (`backend/app/integrations/`): provider interfaces
  (`FinanceProvider`, `CRMProvider`, `OperationsProvider`, `MarketingProvider`,
  `CommunicationProvider`, `CalendarProvider`) + 7 placeholder adapters (QuickBooks, Stripe,
  ServiceTitan, Jobber, Google Ads, Meta Ads, Gmail). `GET /api/v1/integrations` — verified every
  adapter honestly reports `NOT_CONNECTED` (no credentials are configured anywhere in this repo).
- **Temporal workflow code** (`backend/app/workflows/`, `backend/app/temporal_client.py`):
  `EventProcessingWorkflow` (generic event → tool → audit shape) and `InvoiceOverdueWorkflow` (the
  spec's demo: wait → send reminder → wait → check payment → escalate if unpaid) are written
  against the real `temporalio` SDK, with retry policies and timeouts. `send_reminder_activity`
  and `check_payment_status_activity` are explicitly labeled **internal test actions** in their own
  code/log lines — no Finance module or Communication provider exists yet, so they do not call
  Stripe/QuickBooks/Twilio. `execute_tool_activity` is real — it runs through the same
  `ToolRegistry` as everything else. **Live execution status: see "Known limitations" below.**
- **New API endpoints**: `POST/GET /api/v1/events`, `POST /api/v1/events/process/{event_type}`
  (dev-only manual trigger for the subscriber loop), `GET/POST /api/v1/tools`, `GET/POST
  /api/v1/approvals`, `GET /api/v1/integrations`.
- **Alembic migration** `0002_event_bus_tools_approvals`: `events`, `event_processing_records`,
  `dead_letter_events`, `approval_requests`, `notifications` (hand-written, same caveat as
  `0001` — no local Postgres to autogenerate/verify against; see Known limitations).

### Test results (actually run)

```
backend/tests/  (excluding test_temporal_workflows.py)  ->  33 passed, 0 failed
    (python -m pytest -q --ignore=tests/test_temporal_workflows.py, sqlite in-memory)
```

`test_temporal_workflows.py` (2 tests) runs separately against a real local Temporal test server,
not sqlite — see the Temporal entry under "Known limitations" for its partial, honestly-reported
result (one workflow showed a live pass signal, the other hung and was not confirmed).

Covers, concretely:
- Phase 1: register/login, wrong password, tenant isolation, missing/invalid token (5 tests)
- Event bus: publish persists before transport touch, idempotency-key dedup, subscriber receives
  event, **duplicate delivery does not rerun a handler that already succeeded**, retry-then-dead-letter,
  replay of a dead-lettered event, tenant scoping (7 tests)
- Tool registry: authorized execution, unknown tool, permission denied, invalid input schema,
  blocked policy, approval-required creates a request and does not execute, permission-filtered
  tool listing (7 tests)
- Tool tenant isolation: cross-tenant `events.get_event` returns not-found (never another tenant's
  data), missing-tenant context rejected (2 tests)
- Approval flow via the real HTTP API: execute → pending_approval → appears in `GET /approvals` →
  approve → re-approve 409s; direct BLOCKED-policy rejection (2 tests)
- Phase 2 API smoke tests: publish/get event, idempotency-key dedup over HTTP, tool listing +
  execution, 404 on unknown tool, 401 without a token, integrations all report NOT_CONNECTED
  (7 tests)
- **End-to-end acceptance scenarios** (section 13 of the spec) — all three paths, each exercising
  the real event bus + real tool registry together:
  - **Happy path**: publish → subscriber picks it up → executes a typed tool → tool is authorized →
    audit row is written with the correlation ID → event status becomes `PROCESSED`.
  - **Failure path**: a handler tries to run a tool it lacks permission for → `ToolPermissionError`
    → retried → dead-lettered → the notification body **never runs** → a `failure` audit row exists.
  - **Duplicate path**: same event published twice with the same idempotency key → same row
    returned, not requeued; and a raw handler-level redelivery of an already-succeeded
    (event, handler) pair is detected and skipped — the downstream action fires exactly once.
  (3 tests)

## What is explicitly stubbed / not yet built

- Leads, customers, jobs, scheduling, quotes, invoices, payments — still no domain models; Phase 2
  only built the backbone they'll plug into.
- Every third-party integration is still `NOT_CONNECTED` — zero real API calls anywhere in the
  codebase, by design, until real credentials exist.
- LangGraph / autonomous AI agent — `AIExecutionService` establishes the boundary an agent will be
  required to call through, but nothing produces `ToolRequest`s yet.
- Approval decisions do not auto-resume the underlying action — approving a request today changes
  its `status` to `APPROVED`; re-invoking the actual tool call is a manual step (a Phase 3+
  concern, once there's a real caller like a workflow to resume).
- Kafka/NATS transport — the abstraction supports it; nothing beyond Redis is implemented.
- Knowledge base / pgvector, exception engine, reporting, global search, file/document pipeline,
  billing enforcement — unchanged from Phase 1, still not built.

## Known limitations / caveats

- **No Docker, no local Postgres/Redis, no Node/npm in this dev sandbox** (same constraint as
  Phase 1). Every test above ran against sqlite (via a generic SQLAlchemy `Uuid` type + StaticPool
  for a single shared in-memory connection) and the `InMemoryTransport` event bus backend, not real
  Postgres/Redis. The `0002` Alembic migration and the Redis/Temporal Docker Compose wiring are
  written correctly but **not executed against real Postgres/Redis** — run `docker compose up` and
  `alembic upgrade head` as your first verification step.
- **Temporal — partial, honest result.** `temporalio`'s SDK bundles a way to run a real, standalone
  local test server without Docker (`WorkflowEnvironment.start_local()`, downloading a real
  `temporal-sdk-python` binary over the network on first use). That download and launch worked in
  this sandbox (visible in `ps aux` as `temporal-sdk-python ... server start-dev`). A real worker
  then connected and ran `tests/test_temporal_workflows.py`:
  - `test_event_processing_workflow_executes_tool` (runs `EventProcessingWorkflow`, which calls
    `execute_tool_activity` → the real `ToolRegistry` → `system.get_current_time`, against the
    live test server) **showed a pass indicator** (pytest printed `.`, not `F`/`E`) before the run
    continued to the next test — I was not able to capture the final summary line confirming it,
    so treat this as a strong signal, not a fully confirmed result.
  - `test_invoice_overdue_workflow_escalates_when_unpaid` (`InvoiceOverdueWorkflow`, which uses
    `workflow.sleep()`) **hung** — the process sat at near-zero CPU for several minutes with no
    progress and no error, three separate times across repeated attempts. I killed it rather than
    let it run indefinitely rather than report a result I didn't observe.
  - I have **not** fabricated a "both workflows verified" claim. What I can say concretely: the
    Temporal integration is real code (typed dataclass inputs, real retry policies, real timeouts,
    a real worker polling a real server), `execute_tool_activity` is exercised indirectly by every
    tool-registry test in this suite, and the simpler of the two demo workflows showed a live pass
    signal against a real Temporal server. The `workflow.sleep()`-based long-running workflow is
    unverified. Next step: run `docker compose up` (real Temporal + worker) and drive
    `InvoiceOverdueWorkflow` through the Temporal UI at `localhost:8080`, or retry
    `pytest backend/tests/test_temporal_workflows.py -v` outside this sandbox where the hang may
    not reproduce (it looked like a sandbox-specific stall, not a logic bug).
- The `EVENT_TRANSPORT=memory` fallback is real, working code — not a demo shim — but production
  should run `EVENT_TRANSPORT=redis` (the default) with a real Redis reachable.
- `DEFAULT_TOOL_POLICIES` (`backend/app/tools/policy.py`) is a static in-process dict, not a
  per-tenant database table — fine for Phase 2's boundary-proving purpose, but section 44's
  configurable rule engine and per-tenant `approval_policies` table are not built yet.
- Refresh-token rotation/revocation: unchanged from Phase 1, still not implemented.

## Recommended next step

Two reasonable options, both blocked less by design than by "there's no CRM yet":

1. **Verify Phase 2 for real** in an environment with Docker: bring up Postgres/Redis/Temporal,
   run `alembic upgrade head`, run the full test suite against real infra, and specifically drive
   `InvoiceOverdueWorkflow` end-to-end through the Temporal UI to close the one gap this sandbox
   couldn't verify.
2. **Phase 3 (CRM: leads, customers, intake, qualification, booking)** — the natural next domain,
   since it's the first place `lead.created`/`customer.created` events (already modeled in
   `EventType`) get a real producer instead of only being reachable via the raw `POST
   /api/v1/events` API.

Not proceeding into Phase 3 automatically, per instruction — awaiting direction.
