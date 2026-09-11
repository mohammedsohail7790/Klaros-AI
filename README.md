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

### Event Worker (Phase 8)

`backend/app/events/worker.py`. A continuous poll loop (`EventWorker.run_forever`, default 1s —
`EVENT_WORKER_POLL_SECONDS`) around `EventBus.process_pending`, so a published event propagates on
its own — no one needs to call `POST /api/v1/events/process/{event_type}` by hand anymore (that
endpoint still exists for forcing an immediate pass, but is no longer required). This is **not**
the Temporal worker (`backend/app/workers/main.py`, `command: python -m app.workers.main` in
`docker-compose.yml`) — Temporal owns long-running, durable, multi-step *workflows*; the Event
Worker (`command: python -m app.events.worker`, the `event-worker` Compose service) owns turning a
published `Event` row into its subscribers' side effects. In production
(`EVENT_TRANSPORT=redis`) it runs as that separate `event-worker` process against the same Redis
Streams the API publishes to. In the dev/local fallback (`EVENT_TRANSPORT=memory`), it instead
runs as a background task inside the API process itself (started in `app/main.py`'s lifespan) —
`InMemoryTransport`'s state only exists inside one process's memory, so a separate OS process
couldn't see it anyway. Real-time worker metrics (processed/failed/dead-lettered/deduplicated
counts, tick count, per-event-type breakdown) live in `backend/app/events/metrics.py` and are
visible at `GET /api/v1/events/metrics` / the `/events` frontend page, alongside a dead-letter list
with a working retry action.

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

### Integration framework (Phase 2, real providers added Phase 12C)

`backend/app/integrations/`. Provider interfaces (`FinanceProvider`, `CRMProvider`,
`OperationsProvider`, `MarketingProvider`, `CommunicationProvider`, `CalendarProvider`,
`AIProvider`). `GET /api/v1/integrations` (and the frontend's Settings → Integrations page)
reports each adapter's real, live status — `CONNECTED` only after a real, cheap, read-only API
call to the provider actually succeeds (Stripe, Twilio, SendGrid, Anthropic, OpenAI); otherwise
honestly `NOT_CONNECTED`/`ERROR`. No adapter fabricates an API response.

Stripe (real Checkout Session payment links, real webhook-verified payment recording, real
refunds), Twilio (real SMS + delivery-status webhook), and SendGrid (real email) have working
API clients as of Phase 12C — see `INTEGRATIONS.md` for full architecture, required
credentials, and exact testing procedures. QuickBooks Online (real OAuth2 connect flow + invoice
sync, as of Phase 13) is the first provider with a real tenant-owned OAuth client — see below.
Google Calendar, Gmail, Google Ads, Meta Ads, ServiceTitan, Jobber, and outbound-enrichment
providers remain honest stubs.

Phase 12D added a real, tenant-scoped `integration_connections` model + lifecycle service +
API (`app/services/integration_connection_service.py`, encrypted-at-rest credentials via
Fernet) for providers where each tenant owns their own external account. **Phase 13** wired the
first real OAuth provider to it: QuickBooks Online — a real authorization-code + refresh-token
OAuth2 flow, a real connection verifier, and a real invoice-sync capability
(`finance.sync_invoice_to_quickbooks`), all self-contained tested (31 tests) since no real
Intuit developer-app credentials exist in this environment. **Phase 17** added the matching
deposit-payment-sync capability (`finance.sync_deposit_payment_to_quickbooks` + an automatic,
event-driven best-effort attempt) — a real Stripe quote deposit (Phase 15/16) can now be pushed to
QuickBooks as a Payment applied against its job's invoice, once that invoice exists and has itself
been synced; also self-contained tested (30 tests), same credential status. **Phase 18** completed
the loop with refund sync (`finance.sync_refund_to_quickbooks` + the same automatic-attempt
pattern) — a completed Klaros refund now pushes to QuickBooks as a RefundReceipt against the
original payment, once that payment has itself been synced; also self-contained tested (30 tests).
**Phase 19** extended payment sync from deposit-only to ordinary invoice payments too
(`finance.sync_invoice_payment_to_quickbooks`) — a payment allocated to an invoice now syncs the
same way a deposit payment does, and refunds against it already flow through the unmodified
Phase 18 refund sync with zero changes; also self-contained tested (25 tests). **Phase 20** audited
the full accounting lifecycle, confirmed the underlying model already supports a single payment
split across multiple invoices, and removed that Phase 19 restriction with real multi-line
QuickBooks support — also found and fixed a real bug where a payment fully refunded via several
partial refunds never reached `REFUNDED` status. **Phase 21** performed a full production-readiness
audit of the accounting lifecycle and found (via direct reproduction, not inference) two real
concurrency bugs — duplicate payments racing to overpay the same invoice, and duplicate refund
approvals both calling Stripe's real refund API — plus a single-call duplicate-allocation
overpayment bug; all three fixed and re-verified, the concurrent-payment fix specifically against
real PostgreSQL (row-level locking has no SQLite equivalent). **Phase 22** audited the complete
Stripe payment lifecycle end to end (quote → deposit → Checkout → webhook → Payment → Job →
Invoice → QuickBooks, and the refund path) and found one real, high-severity bug: `JobService
.create_job`'s idempotency check had no handling for a concurrent duplicate-key race, so two
concurrent deposit-payment confirmations for the same quote could leave a quote stuck at
`DEPOSIT_PAID` with no Job ever created despite a real Stripe deposit being taken — reproduced
directly under `asyncio.gather` against real PostgreSQL, fixed, and re-verified (4 new tests).
**Phase 23** attempted live Stripe test-mode verification, the explicit next step Phase 22
recommended — `STRIPE_SECRET_KEY`/`STRIPE_WEBHOOK_SECRET` remain confirmed absent, so no live
provider call was possible. An independent re-audit found a second real bug, distinct from Phase
22's concurrency race: a transient (non-duplicate-key) failure inside `JobService.create_job`
during the deposit webhook path left a customer's deposit charged and the quote stuck at
`DEPOSIT_PAID` with no Job — permanently, since neither Stripe's own retry nor a manual webhook
resend could ever recover it, because both the webhook's own dedup check and `QuoteService.
mark_deposit_paid`'s idempotency check treated the stuck state as final. Fixed in both places;
re-verified a redelivery now converges to exactly one Payment, one Job, `CONVERTED` (2 new tests),
with zero regression on a full re-run of both SQLite and real PostgreSQL+Redis. A further audit
pass in the same phase found and fixed a third bug: `PaymentService.request_refund`'s
overcommitment check read the Payment row with no lock, so concurrent refund *requests* against
the same payment could collectively exceed it (5 concurrent $30 requests against a $100 payment
all succeeded in a reliable real-Postgres reproduction) — fixed with `with_for_update=True`,
mirroring Phase 21's identical invoice-overpayment fix. A further re-verification pass closed one
remaining test-coverage gap (no new defect): a new failure-injection test proves QuickBooks
persistence-failure recovery already works as documented (retry sends the identical `request_id`).
**Phase 24** audited the Stripe/QuickBooks provider boundary specifically (OAuth CSRF/state
protection, API contract details, credential encryption, tenant scoping) and found no defect — per
the mission's own instruction not to manufacture one, zero code changes were made; a fresh full
regression run matched the pre-phase baseline exactly on both engines. **Phase 25** made the
provider boundary maximally ready for real credentials: fetched Stripe's current official Checkout
API reference and confirmed the implementation matches exactly, and closed a real test-coverage gap
— no existing test had ever decoded and asserted the actual outgoing Checkout/Refund request body
(amount, currency, metadata), only generic client behavior — 2 new tests now do, both passing on
the first try. Produced the full step-by-step live-verification runbook for both providers
(`INTEGRATIONS.md`). No production-code defect found. Classification: internally production-ready
for controlled live-provider verification, subject to obtaining test/sandbox credentials.
**Phase 27** shifted from payment/refund correctness to operational readiness and found a real
defect: `GET /ready` only ran `SELECT 1` against the database, so it reported healthy even against
a completely unmigrated schema (reproduced directly) — the exact "reports healthy while critical
functionality is unusable" failure mode. Fixed by adding a migration-head check comparing the DB's
`alembic_version` against the code's expected Alembic head.
**Phase 29** found and fixed a real event-loss defect: `PaymentService.record_payment` and
`QuoteService.mark_deposit_paid` both commit their business state, then publish their event
(`PAYMENT_RECEIVED`/`QUOTE_DEPOSIT_PAID`) as a separate call — if that publish failed, the event was
permanently lost even after a fully successful retry, since the retry's own idempotency
short-circuit never re-attempted the publish. Confirmed by direct reproduction; fixed by publishing
unconditionally on both paths, relying on `EventBus.publish`'s existing idempotency-key dedup.
Google Calendar/Gmail/Google Ads/Meta Ads still have no real OAuth client wired to this same model.

Phase 12E hardened the OpenAI/Anthropic provider (`app/services/ai_provider.py`): configurable
timeout/retries/output-size, real retry-with-backoff, real error classification, real
token-usage extraction, defense-in-depth key redaction. Added a general-purpose
`generate_structured()` call and `AIInvocationLog` (a real, tenant-scoped audit+usage trail for
direct AI-provider calls). Built the first real consumer of both: an advisory-only AI
lead-qualification recommendation (`crm.ai_qualify_lead_advisory`) that coexists with — and
never replaces or bypasses — the deterministic scorer in `app/services/scoring.py`. Real
credentials remain unconfigured; see `INTEGRATIONS.md`.

Phase 12F hardened `StripeClient` (configurable timeout/retries, real error classification, a
real idempotency key on checkout creation) and made Stripe the first provider to actually use
the Phase 12D tenant-scoped `IntegrationConnection` model — a tenant can connect their own
Stripe key via Settings → Integrations, verified live against Stripe's real API. Extended
webhook coverage (`payment_intent.payment_failed`, `charge.refunded`) and proved real Stripe
payments flow into the existing Marketing Attribution Loop with no duplicate counting. Found and
fixed a real, exploitable P0: 8 finance approval tools (`finance.approve_refund` and 7 siblings)
had no AI-actor guard — an AI actor could complete a real refund with no human involved; fixed
and covered by 8 regression tests. Real Stripe credentials remain unconfigured; see
`INTEGRATIONS.md`.

### CRM: leads, customers, appointments (Phase 3)

`backend/app/models/crm.py`, `backend/app/services/`, `backend/app/tools/builtin/crm_tools.py` +
`appointment_tools.py`. The first real business domain, built entirely on the Phase 2 backbone:

- **Lead lifecycle**: `POST /api/v1/leads` → validate → normalize phone/email → match an existing
  customer by **exact** normalized email/phone (`services/customer_matching.py` — no fuzzy
  auto-merge, per spec) → persist → publish `lead.created`. Qualification runs asynchronously via
  an event-bus subscriber (`app/events/crm_handlers.py`), never blocking the request.
- **Qualification** (`services/scoring.py`): real, deterministic, rule-based scoring — **not an
  LLM call** (no `ANTHROPIC_API_KEY` configured). Score ≥70 → `QUALIFIED`, <30 → `UNQUALIFIED`,
  else → `REQUIRES_HUMAN`. Stores `score`, `score_version`, and a concise `score_reason`.
- **Customer 360**: `GET /api/v1/customers/{id}/timeline` and `/summary` are built from real rows
  (leads, appointments, notes, audit log) and say so honestly when data is thin — never invented.
  The Finance section shows real invoices/payments (Phase 5) or "No financial history" when there
  are none.
- **Booking**: `backend/app/calendar/` — `CalendarProvider` interface,
  `NotConnectedCalendarAdapter` (external, e.g. Google Calendar — not configured), and
  `InternalTestCalendarAdapter`, a **real** calendar backed by the `appointments` table (fixed
  09:00–17:00 UTC business hours), labeled INTERNAL TEST CALENDAR everywhere it surfaces.
  Double-booking prevention is a real overlap check before every insert/reschedule. **Phase 14**:
  a real, tenant-scoped Google Calendar OAuth2 connect flow + one-way appointment sync
  (`app/integrations/google_calendar_client.py`, `app/services/google_calendar_sync_service.py`)
  pushes already-decided appointments outward to a tenant's own connected Google Calendar —
  additive, not a replacement for the internal scheduling engine above, which remains the source
  of truth for availability/double-booking. See "Quotes/Estimates" below for the identical
  OAuth-state-token/`IntegrationConnection` pattern applied a second time.
- **Communications**: `backend/app/communications/` — `CommunicationProvider` interface,
  `InternalTestCommunicationAdapter` logs every message to `communication_logs` instead of calling
  Gmail/Twilio/SendGrid. Wired into Operations job-status events in Phase 4 (see below); CRM
  booking-time communication is still not wired (see PROJECT_STATUS.md).
- **15 CRM tools** registered on the same `ToolRegistry` as Phase 2 — no CRM code path bypasses
  permission/tenant/schema/policy/audit enforcement.
- **Temporal**: `LeadQualificationWorkflow` wraps the same qualification service the event-bus
  handler uses — see PROJECT_STATUS.md for its (materially improved) live-verification result.

Frontend: `/leads`, `/leads/[id]`, `/customers`, `/customers/[id]`, `/calendar` — real pages against
the real API, no hardcoded data. `/dashboard` now shows real CRM metrics from
`GET /api/v1/crm/metrics` (zeros for an empty tenant, never sample numbers).

### Quotes/Estimates: the pre-work stage + Klaros' first customer-facing page (Phase 14)

`backend/app/models/quote.py`, `backend/app/services/quote_service.py`,
`backend/app/tools/builtin/quote_tools.py`, `backend/app/api/v1/quotes.py` +
`public_quotes.py`. Closes the gap between a Lead and a Job: a `Quote` is a formal, priced
proposal a customer must approve before any work is scheduled.

- **Internal lifecycle** (staff, authenticated): `quotes.create_quote_draft` →
  `quotes.update_quote_draft` (DRAFT only) → `quotes.send_quote` (generates a real, signed
  90-day public view link and delivers it via the existing `InvoiceDeliveryProvider`, extended
  with a `send_quote` method — same internal-test adapter that logs invoice delivery, not a
  parallel path).
- **The public accept/decline flow is Klaros' first page reachable with no login at all** —
  `GET/POST /api/v1/public/quotes/{id}?token=...`. The token is a signed JWT
  (`create_quote_view_token`, reusing the same `JWT_SECRET`/signing mechanism as the Phase 13
  QuickBooks OAuth `state` token) that is the sole source of authority — `tenant_id`/`quote_id`
  come only from the verified token, and the URL's own `{quote_id}` is checked against the
  token's rather than trusted directly. Accepting a quote calls the existing
  `JobService.create_job` to convert it into a real `Job`, idempotently — a replayed accept link
  can never create two jobs.
- **Deterministic expiry**: `quotes.detect_expired_quotes` mirrors the existing AR-aging
  pattern (`ARService.detect_overdue`) rather than a new Temporal workflow.
- Reuses `InvoiceService`'s pricing math (`LineItemInput`/`compute_line_total`/`compute_totals`)
  directly rather than re-deriving it.

Frontend: `/quotes` (list), `/quotes/[id]` (staff detail + Send action), `/quotes/view/[id]`
(the public page — no login, no `AppShell`).

### Quote deposit collection (Phase 15)

`backend/app/services/quote_deposit_service.py`, `backend/app/services/quote_service.py`
(extended), `backend/app/tools/builtin/quote_deposit_tools.py`. Closes the loop: an accepted
quote with a configured deposit (fixed amount or percentage, Decimal-safe) holds at a new
`DEPOSIT_PENDING` status instead of immediately converting to a `Job` — the real `Job` is only
created once a real Stripe payment for the deposit succeeds. A quote with no deposit configured
still converts immediately, exactly as in Phase 14 — this is purely additive.

- The customer pays via a real, hosted Stripe Checkout Session (`POST /api/v1/public/quotes/{id}/
  deposit/checkout`, reusing the existing `quote_view` token — no new token type, no Stripe.js
  needed). `POST /api/v1/webhooks/stripe`'s existing `payment_intent.succeeded` handler branches
  on `metadata.purpose == "quote_deposit"`, records the `Payment` (linked via a new nullable
  `Payment.quote_id`, since a deposit has no `Invoice` to allocate against yet), and advances the
  quote through `DEPOSIT_PAID` into a real `Job`.
- `STRIPE_SECRET_KEY`/`STRIPE_WEBHOOK_SECRET` are unset in this environment — the flow is fully
  built and tested against a real `httpx.MockTransport` and the real webhook HTTP endpoint, but
  live Stripe verification is `BLOCKED BY CREDENTIAL`.
- **Phase 16**: `/quotes/view/[id]` now has a full customer-facing deposit UX on top of this
  backend — deposit notice before accept, a "Pay deposit securely with Stripe" button once
  `DEPOSIT_PENDING`, and paid/converted confirmation views. It never trusts Stripe's return
  redirect as proof of payment, only a re-fetched, webhook-confirmed quote status. Written and
  statically reviewed; `node`/`npm` absent from this sandbox, so it has not been typechecked,
  built, or browser-verified.

### Operations & Delivery: jobs, dispatch, QA, close-out (Phase 4)

`backend/app/models/operations.py`, `backend/app/services/`, `backend/app/tools/builtin/{job,
task_material,document,qa,exception,scope_change,completion,worker}_tools.py`. Extends the delivery
lifecycle: `appointment → job → schedule → dispatch → field execution → documentation → QA →
completion → close-out → invoice.trigger_requested` — entirely on the Phase 2/3 backbone (same
`ToolRegistry`, same event bus, same audit system; no second permissions or event system).

- **Job state machine** (`services/job_state_machine.py`): a fixed transition table. `CLOSED`/
  `CANCELLED` are terminal — invalid jumps (`CLOSED→IN_PROGRESS` etc.) are rejected, not silently
  allowed.
- **Lead→customer→appointment→job conversion** (`services/conversion_service.py`,
  `operations.convert_lead_and_book`): the explicit, idempotent chain section 4 asks for — reuses
  Phase 3's exact-match customer logic, never fuzzy-merges.
- **Assignment & scheduling** (`services/job_transition_service.py`): checks worker
  active/availability, service-type compatibility, and real schedule conflicts (overlapping time +
  same worker) before assigning.
- **Field documentation — real file storage**: `backend/app/storage/` — `ObjectStorageProvider`
  interface, `LocalFilesystemStorageAdapter` (**INTERNAL LOCAL STORAGE**, real bytes on disk,
  MIME/size validation, path-traversal-safe) used until `OBJECT_STORAGE_ENDPOINT` is configured
  (then `NotConnectedObjectStorageAdapter` — no fake S3). Voice notes get
  `transcription_status: NOT_CONFIGURED` — no transcription provider exists yet.
- **QA engine** (`services/qa_service.py`): `complete_qa` re-verifies required tasks/documentation
  itself rather than trusting the caller; a job can never reach `CLOSED` with failed/incomplete QA.
- **Deterministic delay detection** (`services/delay_detection_service.py`): pure timestamp
  comparisons — no LLM, per spec. Exposed as `POST /api/v1/exceptions/detect` (no scheduler runs it
  automatically yet).
- **Completion packet & close-out** (`services/completion_service.py`): the packet is assembled from
  real rows only; `close_job` re-checks QA/tasks/packet itself and emits `job.closed` +
  `invoice.trigger_requested` — picked up by Phase 5's Finance handler to create the actual invoice.
- **34 new `operations.*` tools** on the same `ToolRegistry`, with policies per section 31
  (`reschedule_job` and `request_scope_change_approval` are `APPROVAL_REQUIRED`; almost everything
  else is `AUTO`).

Frontend: `/operations` (real dashboard + open exceptions), `/jobs` + `/jobs/[id]` (the operational
source of truth — status-aware next-action buttons, tasks, materials, real photo/document upload,
QA panel, completion packet, close), `/exceptions`. **Live-verified end to end in a real browser**:
create job → schedule → assign worker → dispatch → en route → on site → start → complete a required
task → upload a real photo → complete field work → pass QA → generate packet → close job → Operations
dashboard correctly showed "1 Completed today." Found and fixed two backend bugs and one frontend bug
during this verification — see PROJECT_STATUS.md for exactly what and how.

### Finance & Back Office: invoices, payments, AR, job costing, cash (Phase 5)

`backend/app/models/finance.py`, `backend/app/services/{invoice,payment,adjustments,ar,collection,
job_costing,cash_forecast,vendor}_service.py`, `backend/app/tools/builtin/{invoice,payment,ar,
job_cost,cash,vendor,adjustment}_tools.py`. Money is `Decimal`/`Numeric` throughout — never `float`
— a deliberate contrast with Phase 4's pre-existing `Job.estimated_*`/`actual_*` float columns,
which Finance reads but never writes a float into.

- **Invoice lifecycle**: `DRAFT → PENDING_APPROVAL/APPROVED → SENT → PARTIALLY_PAID/PAID →
  OVERDUE/VOID/CANCELLED`. Totals are always recomputed server-side from line items — a client-
  supplied total is never trusted. `invoice.trigger_requested` (published on job close-out) creates
  an idempotent DRAFT pulling real `Job.estimated_revenue`/approved `ScopeChange` pricing, or an
  honest "requires review" DRAFT with zero totals if no pricing exists — never a fabricated amount.
- **Approval** (`services/invoice_policy.py`): deterministic thresholds (total > $1,000, discount >
  20% of subtotal, or an unresolved scope change) route through the *existing* `ApprovalRequest`
  model — not a second approval mechanism. Dedicated `approve_invoice`/`reject_invoice` tools both
  resolve the request and transition the invoice in one call.
- **Delivery & payments**: `backend/app/invoice_delivery/` (`InvoiceDeliveryProvider` +
  `InternalTestInvoiceDeliveryAdapter`, records real `communication_logs` rows) and
  `backend/app/payments/` (`PaymentProvider` + `InternalTestPaymentAdapter`, explicitly labeled
  **INTERNAL TEST PAYMENT PROVIDER** — no card is charged, no money moves). Payment recording is
  idempotent on `(tenant_id, provider, external_id)`; invoice `amount_paid`/`amount_due`/`status`
  are always recomputed from the sum of allocations, never incremented.
- **Refunds/credit notes/write-offs**: all `APPROVAL_REQUIRED` by construction — the `create_*`
  tools can only ever land in a pending state; a separate, permission-gated `approve_*` call is the
  only way to complete one. AI can request; AI cannot approve its own request.
- **Job costing** (`services/job_costing_service.py`): writes `JobCost` rows and recomputes the
  *existing* `Job.actual_cost`/`actual_margin` fields — no duplicate columns. Margin-variance
  detection opens a `MARGIN_LEAK` exception via the *existing* exception engine.
- **AR & collections**: `services/ar_service.py` derives aging/balances from `Invoice` rows on
  request — no persisted AR table. `services/collection_service.py` uses a deterministic
  `CollectionAction.scheduled_for` record with on-demand execution — **not** `workflow.sleep()`.
- **Cash forecast** (`services/cash_forecast_service.py`): a real 13-week projection from open
  invoices/vendor bills with HIGH/MEDIUM/LOW confidence per item; starting cash is `NOT_CONNECTED`
  unless `Organization.manual_starting_cash` is explicitly set (labeled MANUAL/INTERNAL TEST DATA).
- **30 new `finance.*` tools** on the same `ToolRegistry`. `send_invoice` is `AUTO` (already gated
  by the invoice's own approval state — see PROJECT_STATUS.md's Known Limitations for why a second
  generic approval gate there was a dead end); `void_invoice`/`record_payout` are
  `APPROVAL_REQUIRED`; `approve_*`/`reject_*` tools are gated by dedicated permissions the AI
  execution boundary's default role does not hold.

Frontend: `/finance`, `/finance/invoices` + `/finance/invoices/[id]`, `/finance/ar`,
`/finance/profitability`, `/finance/cash`; Finance extensions to the Owner Cockpit, Customer 360, and
Job detail pages. **Live-verified end to end in a real browser**: registered a tenant, created a
customer and job, drove it through the full state machine to CLOSED, triggered a real invoice,
auto-approved it under threshold, sent it (internal test delivery), recorded a test payment (internal
test payment provider) → PAID, confirmed Customer 360/Owner Cockpit/Profitability all reflected real
data, forced a second invoice overdue and confirmed AR aging, a real `INVOICE_OVERDUE` exception, and
a scheduled/executed `CollectionAction` with a real communication log. Found and fixed one real
frontend bug during this verification (an `APPROVAL_REQUIRED` 202 response's `detail` wrapper wasn't
being unwrapped, so the UI showed "Invoice sent" for an invoice that hadn't sent) — see
PROJECT_STATUS.md for exactly what and how.

### Marketing & Demand Generation: campaigns, attribution, content, SEO, outbound, nurture (Phase 6)

`backend/app/models/marketing.py`, `backend/app/services/{campaign,attribution,content,seo,local,
outbound,nurture,reactivation}_service.py`, `backend/app/tools/builtin/marketing_{campaign,
attribution,content,seo,outbound,nurture,reactivation,ads}_tools.py`. Reuses `Lead`, `Customer`,
`Job`, `Invoice`, `Payment`, `CommunicationLog`, `OperationsException`, `Event`, `AuditLog` — none of
these are duplicated. Money is `Decimal`/`Numeric` throughout.

- **The Marketing Attribution Loop** (`services/attribution_service.py` +
  `events/marketing_handlers.py`, mirroring `finance_handlers.py`): a lead attributed to a campaign
  gets exactly one `CampaignConversion` row, advanced in place — never duplicated — as real
  `lead.qualified`/`appointment.created`/`job.created`/`job.closed`/`invoice.created`/
  `payment.received` events fire. `campaign_performance` derives spend/leads/qualified/booked/jobs/
  revenue/collected/CAC/ROAS purely from real rows; when the data can't support a number, it returns
  `None` with an explicit note ("Insufficient data: no qualified leads yet", "Attribution incomplete:
  no invoiced revenue yet for this campaign's leads") — never a fabricated `$0.00`.
- **Paid Acquisition / Outbound providers**: `backend/app/marketing_ads/base.py`
  (`GoogleAdsProvider`/`MetaAdsProvider`/`YouTubeAdsProvider`/`LocalServicesAdsProvider` +
  `NotConnected*` adapters) and `backend/app/marketing_outbound/base.py` (`LeadListProvider`/
  `ContactEnrichmentProvider`/`OutboundProvider`/`PermitDataProvider` + `NotConnected*` adapters,
  covering Clay/Apollo/Instantly/permit data) — every adapter reports `NOT_CONNECTED` honestly; no
  fake campaign data, contact list, or enrichment result is ever returned.
- **Content Engine** (`services/content_service.py`): a single `MarketingContent` row carries
  `IDEA → DRAFT → PENDING_APPROVAL → APPROVED → SCHEDULED → PUBLISHED/ARCHIVED`. Publishing goes
  through the *existing* `ApprovalRequest` model, gated by the new `APPROVE_MARKETING_CONTENT`
  permission — AI can request approval, AI cannot approve its own request.
  `generate_draft_from_job` grounds a draft in a real completed job's real service/notes/photo
  count — never an invented result, photo, or customer quote. No `ANTHROPIC_API_KEY`/
  `OPENAI_API_KEY` is configured, so caption/SEO-page generation
  (`services/ai_content_service.py`) is an honestly-labeled deterministic template, not a fabricated
  LLM response.
- **Local & Organic / SEO** (`services/seo_service.py`, `services/local_service.py`): SEO pages are
  always `DRAFT` / `ai_generated=True`; publishing is a separate `APPROVAL_REQUIRED` action.
  Keyword rankings stay `NULL` unless a human enters a real observation — no rank-tracking provider
  is connected.
- **Outbound & List Building / Nurture & Reactivation** (`services/outbound_service.py`,
  `services/nurture_service.py`, `services/reactivation_service.py`): contact dedup reuses the exact
  `normalize_email`/`normalize_phone` helpers Lead/Customer matching already uses. Sequences use a
  deterministic `scheduled_for` record + on-demand execution — the same safe pattern as Phase 5's
  `CollectionAction`, explicitly not `workflow.sleep()`. Reactivation/nurture candidate selection is
  pure, deterministic timestamp/status comparison (stale leads, unbooked-qualified leads, customers
  with no job in 180+ days) — no LLM.
- **Marketing exceptions**: extends the *existing* `OperationsException` engine with
  `CAMPAIGN_OVERSPEND`/`LOW_CONVERSION`/`HIGH_CAC`/`ATTRIBUTION_GAP`/`CONTENT_APPROVAL_DELAY`/
  `FAILED_PUBLICATION`/`REACTIVATION_FAILURE` — no second exception system.
- **~35 new `marketing.*` tools** on the same `ToolRegistry`. Outbound/nurture enrollment and
  execution are `AUTO` (internal test communication provider only, no real external send exists yet
  — see PROJECT_STATUS.md for why this deliberately avoids the exact dead end Phase 5 found in
  `finance.send_invoice`); content/SEO publishing and review responses remain `APPROVAL_REQUIRED`.

Frontend: `/marketing`, `/marketing/campaigns[/[id]]`, `/marketing/content`, `/marketing/seo`,
`/marketing/outbound`, `/marketing/reactivation`; a Marketing section on the Owner Cockpit.
**Live-verified end to end in a real browser**: created a real campaign and recorded real spend,
created and attributed a real lead, qualified it, booked it, created and closed a real job, triggered
a real invoice, sent it, recorded a real test payment → PAID — then confirmed the campaign detail page
showed real, derived (never fabricated) numbers: spend $200.00, 1 lead/1 qualified, 1 appointment/1
job, 1 job closed, $900.00 revenue, $900.00 collected, **CAC $200.00, ROAS 4.50x** — matched on the
Owner Cockpit. Generated a real content draft from that job's real photo/data and drove it through
approval; created a real outbound list/contact; created a real reactivation campaign and confirmed
"identify inactive customers" honestly returned zero candidates (every customer had a recent job).
Found and fixed one real bug during this verification (the dev sqlite database's tables were created
before the Marketing models existed, so the first live call 500'd on `no such table: campaigns`) — see
PROJECT_STATUS.md for exactly what and how.

### Approval orchestration + real AI provider (Phase 9)

`backend/app/services/approval_execution_service.py`. Closes the long-standing "approval dead end":
approving a `ToolRegistry`-created `ApprovalRequest` used to only flip that request's own status.
`ApprovalExecutionService.approve()` now calls `execute_approved()`, which calls
`ToolRegistry.execute(..., skip_approval_gate=True)` — the same registry, same permission/tenant/schema
checks, same audit path every tool call goes through; `skip_approval_gate` only skips the
`APPROVAL_REQUIRED` branch, not the `BLOCKED` check. State transitions (`PENDING → APPROVED/REJECTED`,
`NOT_STARTED → EXECUTING`) are DB-level compare-and-swap (`UPDATE ... WHERE <expected-state>`, checked
via `rowcount`), not Python locks — safe under real concurrent requests. Self-approval is blocked both
structurally (`ApproveAction` rejects any `actor_type=AI` caller outright) and per-request
(`ApprovalExecutionService` rejects a requester approving their own request). See `/approvals`
(frontend) and `POST /api/v1/approvals/{id}/approve|reject|retry`.

`backend/app/services/ai_provider.py`. `AIProvider` ABC with `DeterministicAIProvider` (the only one
ever active with no API key configured — checked in code via `is_connected`, not just documented),
`AnthropicAIProvider`, `OpenAIAIProvider` (real `httpx` call bodies, selected via `AI_PROVIDER` env var
— `auto`/`deterministic`/`anthropic`/`openai`). Output is a validated, structured
`{summary, insights: [{entity_id, text}]}` shape — never free text, never a number or id an LLM could
invent — and `MorningBriefService` cross-checks every `entity_id` a response references against the real
deterministic insight list, dropping anything that doesn't match. Any malformed response, timeout, or
provider error falls back to the deterministic brief silently; `mode=AI` is only ever set after a real
call actually succeeded and validated. `insights.execute_recommendation` now correctly handles an
`APPROVAL_REQUIRED` underlying tool by creating a real `ApprovalRequest` and linking the recommendation
to it, instead of failing. See `/morning-brief` (provider/model attribution shown inline) and
`/ai-activity` (a filtered view over the existing `AuditLog`, not a second audit store).

### Configurable autonomy + notification orchestration (Phase 10)

`backend/app/services/policy_service.py`. `PolicyService.resolve()` is now the only place a tool's
effective policy is determined — `ToolRegistry.execute()` calls it on every single execution, including
on an approval's resume. Resolution order: `SYSTEM_BLOCKED_TOOLS` (a platform floor no tenant can
override — `customer.delete`/`finance.delete_invoice`/`operations.delete_job`) → a persisted
`TenantToolPolicy` row for that tenant+tool, if any → the static `DEFAULT_TOOL_POLICIES` fallback.
Writes use a DB-level `version` compare-and-swap, never a Python lock, and every change is audited via
the existing `AuditLog`. See `/settings/automation` (frontend) and
`GET/PUT /api/v1/automation/policies/{tool_name}`, `POST /api/v1/automation/policies/reset`.

`backend/app/services/notification_service.py`. The only place a `Notification` row is ever created.
In-app delivery is the row itself; email/SMS are real, optional `CommunicationProvider` dispatch
attempts gated by per-user `NotificationPreference` rows, honestly a no-op today since no
SendGrid/Twilio credentials are configured. `app/events/notification_handlers.py` subscribes to the
approval lifecycle, exceptions, payments, new leads, and a new `EventType.MORNING_BRIEF_GENERATED` event
— delivered automatically by the same Phase 8 `EventWorker`, deduplicated via a real
`UNIQUE(tenant_id, dedupe_key)` constraint (never application-side counting). See the notification bell
in `AppShell`, `/settings/automation`'s Notifications grid, and `GET /api/v1/notifications`,
`GET /api/v1/notifications/unread-count`, `POST /api/v1/notifications/{id}/read|dismiss`,
`POST /api/v1/notifications/read-all`, `GET/PUT /api/v1/notifications/preferences`.

The AI never decides whether an action is safe — `insights.execute_recommendation` already called
`ToolRegistry.execute()` for its underlying tool (Phase 8/9), and that now resolves the tenant's real
policy automatically; no new code path was needed for "AI recommendations obey policy." `SetPolicy`/
`ResetPolicy` additionally reject any `actor_type=AI` caller outright, since `Role.MANAGER` (the role
`AIExecutionService` always assigns) legitimately holds `MANAGE_AUTOMATION_POLICIES` for its human
members — permission alone isn't enough to keep the AI from changing its own boundaries.

## Local setup

Prereqs: Python 3.12, Node 20, Docker (for Postgres/Redis/Temporal). If you don't have them
system-wide, both can be fetched portably without touching your system install:

```bash
# Python 3.12, via uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Node 20, portable (no sudo, no system Node touched)
mkdir -p ~/.local/node && cd ~/.local/node
curl -sL https://nodejs.org/dist/v20.17.0/node-v20.17.0-darwin-arm64.tar.gz | tar -xz --strip-components=1
export PATH="$HOME/.local/node/bin:$PATH"   # add to your shell profile to persist
```

(Swap `darwin-arm64` for your platform — e.g. `linux-x64`, `darwin-x64`.)

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
backend (http://localhost:8000), worker, and frontend (http://localhost:3000). See
`DOCKER_DEPLOYMENT.md` for the full command reference (migrations, health/readiness
checks, logs, inspecting each service, restarts, persistence, dev reset) — note that
these Docker commands are not verified against a real daemon in this project's history
(see `PRODUCTION_AUDIT.md`'s Phase 12B section); Postgres and Redis themselves ARE
verified, just via real non-Docker binaries rather than containers.

## Running tests

```bash
cd backend
source .venv/bin/activate
python -m pytest -q
```

Tests run against an in-memory SQLite database (no external services required) — **109 passing** —
and cover: registration/login, tenant isolation (auth, CRM, and Operations — leads/customers/
appointments/jobs/workers/exceptions), event publish/subscribe/idempotency/retry/dead-letter/replay,
tool registry authorization (permission/tenant/schema/policy), the approval boundary (AUTO /
APPROVAL_REQUIRED / BLOCKED), CRM (lead creation + dedup, customer matching, qualification scoring,
booking + double-booking prevention, timelines), Operations (job state-machine transitions incl.
invalid-transition rejection, assignment incl. schedule-conflict/inactive-worker rejection, tasks,
materials, file upload incl. size/type validation, QA gating, exception dedup, delay detection,
job-status communications, completion packet + close-out incl. the `invoice.trigger_requested`
event), and full end-to-end acceptance scenarios — including the complete
appointment→job→schedule→assign→dispatch→field-execution→QA→completion→close-out→invoice-trigger
scenario — per `PROJECT_STATUS.md`.

Temporal workflow tests (`tests/test_temporal_workflows.py`) are separate: they spin up
temporalio's ephemeral local test server (a real standalone binary, downloaded over the network —
no Docker needed) rather than running against sqlite. They now pass as part of the normal full
`pytest -q` run (no `--ignore` needed) — a previous, long-standing hang was root-caused during the
Phase 11 production audit (`PRODUCTION_AUDIT.md` P1-1) to a wrong Temporal SDK API call, not a
platform limitation, and fixed.

### Frontend checks

```bash
cd frontend
npm install
npx tsc --noEmit   # typecheck
npm run build      # production build
```

## Database migrations

Alembic is configured (`backend/alembic/`). `0001_initial_schema` creates `organizations`,
`users`, and `audit_logs`. `0002_event_bus_tools_approvals` creates `events`,
`event_processing_records`, `dead_letter_events`, `approval_requests`, and `notifications`.
`0003_crm_domain` creates `leads`, `customers`, `customer_notes`, `appointments`, and
`communication_logs`. `0004_operations_domain` creates `jobs`, `workers`, `job_tasks`,
`job_attachments`, `job_materials`, `purchase_orders`, `purchase_order_items`, `scope_changes`,
`operations_exceptions`, `job_qa`, `completion_packets`, and `customer_signoffs`.
`0005_finance_domain` creates `invoices`, `invoice_line_items`, `payments`, `payment_allocations`,
`refunds`, `credit_notes`, `credit_note_line_items`, `writeoff_requests`, `job_costs`, `vendors`,
`vendor_bills`, `payouts`, `cash_forecasts`, `cash_forecast_items`, `collection_actions`, and adds
`organizations.manual_starting_cash`. `0006_marketing_domain` creates `campaigns`, `marketing_spend`,
`marketing_spend_allocations`, `marketing_lead_sources`, `lead_attributions`, `campaign_leads`,
`campaign_conversions`, `marketing_content`, `content_assets`, `content_variants`,
`content_publications`, `content_performance`, `seo_pages`, `seo_keywords`, `seo_opportunities`,
`local_listings`, `local_reviews`, `local_reputation_events`, `outbound_lists`, `outbound_contacts`,
`outbound_sequences`, `outbound_steps`, `outbound_enrollments`, `outbound_activities`,
`nurture_sequences`, `nurture_enrollments`, `nurture_activities`, `reactivation_campaigns`, and
`reactivation_candidates`. `0007_retention_domain` creates `customer_lifecycle_profiles`,
`retention_opportunities`, `service_reminders`, `review_requests`, `customer_feedback`,
`referral_programs`, `referral_codes`, `referrals`, `referral_rewards`, `customer_risk_signals`,
`advocate_candidates`, `retention_campaigns`, `retention_enrollments`, and `retention_activities`.
`0008_event_worker_and_morning_brief` adds `last_attempt_at`/`processed_at` to
`event_processing_records`, `replayed_at` to `dead_letter_events`, `morning_brief_enabled`/
`morning_brief_local_time`/`morning_brief_timezone` to `organizations`, and creates `morning_briefs`,
`morning_brief_insights`, and `morning_brief_recommendations`.
`0009_approval_orchestration` adds `requested_by_role`, `decided_at`, `execution_status`,
`execution_result`, `execution_error`, `executed_at`, `execution_attempts`, and `idempotency_key` to
`approval_requests`, plus `approval_request_id` to `morning_brief_recommendations`.
`0010_morning_brief_ai_provider` adds `ai_provider`, `ai_model`, and `ai_generation_ms` to
`morning_briefs`.
`0011_automation_policy_and_notifications` creates `tenant_tool_policies` and
`notification_preferences`, and extends `notifications` with `recipient_id`, `type`, `priority`,
`entity_type`, `entity_id`, `channel`, `status`, `dedupe_key`, `sent_at`, `error`, plus a
`UNIQUE(tenant_id, dedupe_key)` constraint.
Do not hand-edit the schema — add a new migration:

```bash
alembic revision --autogenerate -m "description"
alembic upgrade head
```

## Environment variables

See `.env.example`. Third-party integration credentials (QuickBooks, Stripe, Twilio, SendGrid,
Google Ads, OpenAI, Anthropic, etc.) are listed but left blank by default. As of Phase 12C,
Stripe/Twilio/SendGrid/OpenAI/Anthropic have real, working API clients — setting the matching
env vars and restarting is enough to connect them for real (see `INTEGRATIONS.md` for exactly
which vars each provider needs and how to verify). As of Phase 13, QuickBooks also has a real
OAuth2 client — set `QUICKBOOKS_CLIENT_ID`/`QUICKBOOKS_CLIENT_SECRET`/`QUICKBOOKS_REDIRECT_URI`
(a real Intuit developer app) and restart to let tenants connect their own QuickBooks company
via Settings → Integrations. Google Ads/etc. are not yet implemented regardless of what's set.
The app must never claim a disconnected integration is working.

## Troubleshooting

- **`str | None` / `TypeError` on startup**: you're on Python < 3.10. Use `uv venv --python 3.12`.
- **`email-validator is not installed`**: `pip install pydantic[email]` (already pinned in
  `requirements.txt`).
- **bcrypt `password cannot be longer than 72 bytes` / passlib crash**: caused by
  `passlib==1.7.4` + `bcrypt>=4.1`. This repo pins `bcrypt==4.0.1`, which is compatible.
- **Frontend gets a CORS error in the browser console**: `CORS_ORIGINS` (backend) must include the
  exact origin (scheme+host+port) the frontend is served from. The default covers
  `http://localhost:3000` (Docker Compose's frontend port); if you run the frontend on a different
  port for manual testing, add it to `CORS_ORIGINS` and restart the backend.
- **`npm audit` reports a high-severity postcss advisory**: it's nested inside `next@14.2.35`'s own
  bundled `postcss` (a dev-time source-map issue in Next's build tooling), not this repo's direct
  dependency. Clearing it requires a Next 16 major upgrade — tracked, not silently ignored (see
  `PROJECT_STATUS.md`).
