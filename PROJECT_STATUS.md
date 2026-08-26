# Klaros AI — Project Status

Last updated: 2026-08-26 (Phase 3 checkpoint)

## What's actually built and verified — Phase 1 (unchanged)

- **Multi-tenant data model**: `organizations`, `users`, `audit_logs` with `tenant_id` on every
  tenant-owned row.
- **Auth**: registration (creates an org + OWNER user), login, JWT access/refresh tokens with
  bcrypt password hashing.
- **RBAC**: permission matrix, enforced via `require_permission()`.
- **Tenant isolation enforcement**: every query filters by `tenant_id` taken from the decoded JWT.
- **Docker Compose**: postgres (pgvector), redis, temporal + temporal-ui, backend, worker, frontend.

## What's actually built and verified — Phase 2 (unchanged)

- **Event bus** (`backend/app/events/`): Postgres-backed durable event store + pluggable transport
  (`RedisStreamTransport` for prod, `InMemoryTransport` — same interface, real, used in tests/dev).
  Publish-time idempotency-key dedup, handler-level dedup via `event_processing_records`, retries
  with dead-lettering, replay, correlation IDs.
- **Tool / MCP framework** (`backend/app/tools/`): `ToolRegistry.execute()` enforces lookup →
  permission (RBAC) → tenant-scope → Pydantic schema validation → action policy
  (AUTO/APPROVAL_REQUIRED/BLOCKED) → execution → audit, for every tool call, from any caller.
- **Approval boundary**: `APPROVAL_REQUIRED` tools create a persisted `ApprovalRequest` and never
  execute; `BLOCKED` tools never execute.
- **AI execution boundary** (`backend/app/ai/`): the only surface a future AI agent will be allowed
  to call tools through.
- **Integration framework** (`backend/app/integrations/`): provider interfaces + placeholder
  adapters, all honestly reporting `NOT_CONNECTED`.
- **Temporal workflow code**: `EventProcessingWorkflow`, `InvoiceOverdueWorkflow` against the real
  SDK. See the Temporal entry under Phase 3 below for updated, more conclusive live-verification
  results (this checkpoint re-ran and isolated the Phase 2 tests too).

## What's actually built and verified — Phase 3 (CRM)

### Domain model

`backend/app/models/crm.py` — tenant-scoped `Lead`, `Customer`, `CustomerNote`, `Appointment`; plus
`backend/app/models/communication.py` — `CommunicationLog`. Migration `0003_crm_domain`
(hand-written, same Postgres-not-available caveat as `0001`/`0002` — see Known limitations).

### Lead lifecycle

`POST /api/v1/leads` → `LeadService.create_lead()` (`backend/app/services/lead_service.py`):
validates → normalizes phone/email → matches an existing customer by exact normalized email/phone
(`backend/app/services/customer_matching.py` — **exact match only, deliberately no fuzzy
auto-merge**, per the spec's explicit instruction) → persists → publishes `lead.created`. The API
call returns immediately; qualification happens asynchronously via an event-bus subscriber
(`backend/app/events/crm_handlers.py`), never blocking the request.

**Qualification** (`backend/app/services/scoring.py`, `qualification_service.py`) is real,
deterministic, rule-based scoring (urgency, service/location presence, returning-customer history,
estimated value, source quality) — **explicitly labeled as such, not dressed up as an LLM call**,
since no LLM is connected (`ANTHROPIC_API_KEY` is blank in this repo). Score ≥70 → `QUALIFIED`,
<30 → `UNQUALIFIED`, else → `REQUIRES_HUMAN`. Every score stores `score`, `score_version`, and a
concise `score_reason` — no hidden chain-of-thought, an auditable one-paragraph rationale.
Verified live in the browser: a HIGH-urgency, referral-sourced lead scored 90/100 and moved to
`QUALIFIED` in real time against the running API.

Events emitted: `lead.created`, `lead.qualified`, `lead.unqualified` (all with `entity_id`,
`correlation_id`; `lead.created` carries an idempotency key derived from the lead id).
`lead.updated`/`lead.enriched`/`lead.assigned`/`lead.booked`/`lead.lost`/`lead.converted` are
defined in `EventType` for future use but nothing publishes them yet — only what's actually wired
is claimed here.

### Customer 360

`GET /api/v1/customers/{id}`, `/timeline`, `/summary`, `POST .../notes`. The timeline
(`crm.get_customer_timeline` tool) is built from real rows — leads, appointments, notes, audit
log — sorted chronologically, never fabricated. The AI summary (`crm.generate_customer_summary`)
is built from the same real data and **says "insufficient data" honestly** when a customer has no
leads/appointments yet (verified live: a fresh customer with only a booked appointment produced
"0 lead(s) on record... Finance module not connected yet" rather than inventing history). Finance
sections (quotes/invoices/payments) explicitly render **"Finance module not connected yet."**

### Booking / calendar

`backend/app/calendar/`: `CalendarProvider` interface, `NotConnectedCalendarAdapter` (external —
Google Calendar etc., not configured), and `InternalTestCalendarAdapter` — a **real, working**
calendar backed by the `appointments` table, explicitly labeled INTERNAL TEST CALENDAR everywhere
it surfaces (API responses, UI copy, docstrings). Business hours fixed at 09:00–17:00 UTC in
30-minute slots (documented simplification — no per-tenant business-hours config yet).

Double-booking prevention is real: an overlap check (`start < other.end AND end > other.start`)
runs before every insert/reschedule, scoped per `assigned_user_id`. Verified via tests and live in
the browser (booking removed the slot from `GET /availability` immediately). Idempotency keys
prevent duplicate appointments from a repeated request. Booking a customer that doesn't belong to
the caller's tenant is rejected — a real tenant-isolation gap the test suite caught and a fix
closed (see `internal_test_adapter.py`'s customer-ownership check).

`crm.create_appointment`/`cancel_appointment`/`reschedule_appointment` publish
`appointment.created`/`cancelled`/`updated`, which a subscriber turns into an owner notification —
verified end-to-end in `test_crm_e2e.py` and live (booking in the browser created a real
`Notification` row).

### Communications

`backend/app/communications/`: `CommunicationProvider` interface, `InternalTestCommunicationAdapter`
(real — logs every message to `communication_logs`, does not call Gmail/Twilio/SendGrid). Template
enum (`MessageTemplate`) covers the five templates the spec lists. **Not wired into the booking flow
yet** — no code path currently calls `send_email`/`send_sms` automatically (e.g. no appointment
confirmation SMS fires today). This is honestly listed under "stubbed" below, not silently assumed.

### CRM tools (ToolRegistry)

`backend/app/tools/builtin/crm_tools.py`, `appointment_tools.py`: `crm.create_lead`, `get_lead`,
`update_lead`, `search_leads`, `qualify_lead`, `create_customer`, `get_customer`,
`update_customer`, `search_customers`, `get_customer_timeline`, `create_note`,
`generate_customer_summary`, `check_availability`, `create_appointment`, `cancel_appointment`,
`reschedule_appointment` — 15 tools, all going through the same `ToolRegistry` enforcement pipeline
as Phase 2's tools. Nothing in Phase 3 bypasses it — API routers call tools, not the DB directly
(the one exception: `GET /api/v1/crm/metrics` and `GET /api/v1/appointments` read directly, since
they're pure read/aggregate queries with no side effect, the same pattern already used for
`GET /api/v1/integrations` in Phase 2).

### Temporal — LeadQualificationWorkflow

`backend/app/workflows/definitions.py`'s `LeadQualificationWorkflow` wraps `qualify_lead_activity`,
which calls the exact same `LeadQualificationService` the event-bus handler uses. **This is the
first Temporal workflow in this project confirmed passing with a clean, captured test summary**
(see Known limitations — much stronger evidence than Phase 2's Temporal results).

### API

`/api/v1/leads`, `/api/v1/customers`, `/api/v1/appointments` (+ `/availability`),
`/api/v1/crm/metrics`. Pagination (`limit`/`offset`), filtering (status/source/query), tenant
isolation, and RBAC permission checks all live and tested.

### Frontend

`/leads`, `/leads/[id]`, `/customers`, `/customers/[id]`, `/calendar` (day view), and `/dashboard`
updated with real CRM metrics (`GET /crm/metrics` — zeros for an empty tenant, not sample data).
**This is the first checkpoint where the frontend has been installed, typechecked, production-built,
and driven live in a real browser against the real backend** (Node 20 was not available in earlier
sessions; it is now — see Known limitations for how). Verified in-browser: register → cockpit shows
real zeros → create lead → qualify (90/100, live score) → create customer → search-as-you-type →
book an appointment (slot disappears from availability) → Customer 360 timeline shows the
appointment → AI summary generated live → note added and appears in the timeline instantly. No
console errors during the full flow (one CORS error was hit and fixed — see below).

### Test results (actually run)

```
backend/tests/  (excluding test_temporal_workflows.py)  ->  61 passed, 0 failed
    (python -m pytest -q --ignore=tests/test_temporal_workflows.py, sqlite in-memory)
```

New in this checkpoint (28 tests beyond Phase 2's 33): phone/email normalization and exact-match
customer matching; lead creation idempotency (service-level and over HTTP, including the spec's
literal "same webhook twice" scenario); qualification scoring and thresholds; lead search/filter/
pagination; customer creation, notes, timeline; **tenant isolation for leads, customers, and
appointments** (cross-tenant read → 404, cross-tenant customer_id on booking → rejected); booking
creation, double-booking rejection (same technician, overlapping time), non-overlapping bookings
succeeding, reschedule-into-conflict rejection, cancel-then-rebook, appointment idempotency keys;
availability correctly excludes booked slots (including a real bug fix — sqlite drops datetime
timezone info on round-trip, unlike Postgres, which broke slot-overlap comparison until normalized);
`lead.created` → event-bus qualification → `lead.qualified`, verified via direct event-bus
processing; the full section-25/28 end-to-end scenario (lead → qualify → customer → availability →
book → notification → audit → timeline) as one test; CRM metrics reflecting real data over HTTP.

`test_temporal_workflows.py` — see the Temporal section below for a materially clearer result than
Phase 2 had.

Frontend: `npx tsc --noEmit` — 0 errors. `npm run build` — succeeds (10 routes, all
static/prerendered except the two dynamic detail pages, as expected).

## What is explicitly stubbed / not yet built

- **Communications are not wired into any CRM flow.** The adapter is real and logs to
  `communication_logs`, but nothing calls it automatically yet — no appointment-confirmation SMS,
  no lead follow-up email. A future automation, not a Phase 3 claim.
- **AI qualification is deterministic scoring, not an LLM call.** Matches the spec's required
  output shape so swapping in a real model is a scoring-function change later, not an
  architecture change — but it is not "AI" in the LLM sense today, and this doc says so plainly.
- Lead conversion is not automatic — booking an appointment does not retroactively link a lead's
  `customer_id`, and there's no explicit "convert lead to customer" action yet; a lead and a
  customer created independently (as in the live-browser test) stay two separate records unless
  matched by email/phone at creation time.
- Approval decisions still don't auto-resume an underlying action (unchanged from Phase 2) —
  irrelevant to Phase 3 since every CRM tool's default policy is AUTO (nothing here is a financial
  action yet).
- Everything Finance/Marketing/full Operations — still not built, per this phase's explicit scope.
- Per-tenant business hours/timezone/service-catalog/service-area configuration — the calendar and
  scorer use fixed defaults, documented inline where they matter
  (`internal_test_adapter.py`, `scoring.py`).

## Known limitations / caveats

- **No Docker, no local Postgres/Redis in this dev sandbox** (same as Phase 1/2) — tests ran
  against sqlite; the `0003` migration is written correctly for Postgres but not executed against
  it. Run `docker compose up` and `alembic upgrade head` as your first real-infra check.
- **Node 20 was installed this session** (a portable download to `~/.local/node`, no system
  changes, no `sudo`) specifically so the frontend could finally be typechecked, built, and
  exercised live — this was not possible in the Phase 1/2 checkpoints. `npm audit` still reports
  one high-severity advisory nested inside `next@14.2.35`'s own bundled `postcss` (a dev-time
  source-map path-traversal issue in Next's build tooling) that only clears on a Next 16 major
  upgrade — out of scope for this phase; not a runtime/production exposure for a typical deployment,
  but noted rather than ignored.
- **Temporal — materially better result than Phase 2, still incomplete.** This checkpoint isolated
  each workflow test individually instead of running the whole file at once:
  - `test_event_processing_workflow_executes_tool` — **1 passed** (clean, captured summary line,
    0.27s), run alone against a real local Temporal test server.
  - `test_lead_qualification_workflow_scores_a_real_lead` (new this phase) — **1 passed** (clean,
    captured summary line, 0.66s), same real server, and independently confirmed the qualified
    lead's DB row was updated by the workflow.
  - `test_invoice_overdue_workflow_escalates_when_unpaid` — still **hangs**, reproduced 3 times
    across Phase 2 and Phase 3 sessions. It is the only workflow using `workflow.sleep()`; both
    workflows that don't use `workflow.sleep()` pass cleanly and repeatably. That pattern points at
    a sandbox-specific interaction between this environment and Temporal's time-skipping/sleep
    handling, not a bug in `InvoiceOverdueWorkflow`'s logic — but I'm reporting the pattern, not
    asserting the cause with certainty. Next step: retry outside this sandbox, or test with
    `WorkflowEnvironment.start_time_skipping()` instead of `start_local()`.
  - Running all three tests in the same pytest process still reproduces the hang on the third
    (sleep-based) test, so **the worker/task-queue combination in `run(...)` order matters** — a
    real finding worth investigating further, not just a flake.
- CORS: the live browser test initially failed with a CORS preflight error because the ad-hoc test
  port (3001) wasn't in `CORS_ORIGINS`'s default list — fixed by setting `CORS_ORIGINS` for that
  run. Not a code bug; `.env.example`'s default (`http://localhost:3000`) matches the Docker Compose
  frontend port, so this only came up because of the throwaway port chosen for manual testing here.
- `DEFAULT_TOOL_POLICIES` is still a static in-process dict (unchanged from Phase 2).
- Refresh-token rotation/revocation: still not implemented (unchanged from Phase 1).

## Recommended next step

**Phase 4 (Operations & Delivery)** is the natural next domain per the original roadmap — jobs,
scheduling/dispatch building on this phase's calendar, and the field-execution/documentation flow —
now that Phase 3 gives it real leads, customers, and appointments to attach to. Before that, closing
the Temporal `workflow.sleep()` hang (or at least reproducing/ruling it out outside this sandbox)
would remove the last real unknown in the orchestration layer.

Not proceeding into Phase 4 automatically, per instruction — awaiting direction.
