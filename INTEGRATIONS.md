# Klaros AI — External Integrations (Phase 12C)

## Phase 13 addendum — Company Memory, no new external credential

Built a governed, tenant-scoped, auditable Company Memory system (`app/models/company_memory.py`,
`app/services/company_memory_service.py`) closing the persistent "Human & Owner Input → AI Context"
loop the reference architecture calls for. No new external credential — this is entirely PostgreSQL-
backed (migrations `0033`/`0034`), reusing the existing RBAC/audit/AI-provider infrastructure.

**Three distinct concepts, kept genuinely separate, not blurred together**:
- **Knowledge** (`app/models/knowledge.py`) — source-backed documents the owner wrote out in full
  (pricing policy, brand voice), retrieved by pgvector semantic similarity to a query.
- **Company Memory** (this phase) — short, structured, KEYED facts/preferences
  (`preferred_appointment_time` → `morning`), retrieved in full every time (no embedding, no similarity
  search — Rule 17 was explicit that structured retrieval is correct here, not vector search).
- **Business data** (CRM/Finance/Operations models) — ordinary transactional records. Memory never
  duplicates a KnowledgeChunk or copies an ordinary business record into itself.

**Governance is the whole point**: AI can only ever *propose* a memory candidate
(`CompanyMemoryService.propose_memory`, always `status=PENDING`) — never write an ACTIVE row directly.
Only a human, through `create_memory` (an owner stating their own preference) or `confirm_memory` (an
owner approving an AI-proposed candidate), can make something durable memory. A 5-source authority chain
(`OWNER_EXPLICIT > OWNER_CORRECTION > OWNER_APPROVAL > SYSTEM_DERIVED > AI_PROPOSED`) prevents a
lower-authority write from silently overwriting a higher-authority one's active value for the same key.

Wired into the Morning Brief's AI-mode enrichment prompt as a new, separately-fenced `--- BEGIN COMPANY
MEMORY ---` data block (never instructions — tested directly against the prompt builder with a
deliberately malicious memory value). Real database-level concurrency protection (a partial unique
index — `uq_company_memories_one_active_per_key` — not a Python lock) verified against real PostgreSQL:
10 genuinely concurrent writes for the same key, exactly 1 survives ACTIVE.

## Phase 12 addendum — Knowledge Layer upgraded to real PostgreSQL pgvector

Upgraded `KnowledgeChunk.embedding` from a portable JSON float array (Python-side cosine-similarity
ranking) to a real, fixed-width PostgreSQL `vector(1536)` column with an HNSW cosine-distance ANN index
— genuine database-side vector search. 1536 is `OpenAIEmbeddingProvider`'s real, verified dimension
(`text-embedding-3-small`).

**Requirement for any real PostgreSQL deployment**: the `vector` extension (pgvector) must be
installable on the target Postgres instance. Migration `0032` runs `CREATE EXTENSION IF NOT EXISTS
vector` itself — on self-hosted PostgreSQL this "just works" as long as the pgvector package/extension
files are present on the server (they are on this sandbox's local Postgres.app install, version 0.8.0,
confirmed via `pg_available_extensions`). **On a managed provider** (RDS, Cloud SQL, Supabase, Neon,
etc.), pgvector is usually available but sometimes requires the extension to be allow-listed or enabled
through the provider's own console/API before `CREATE EXTENSION` will succeed inside the database
itself — this is a deployment-environment step, not something this migration can do for you; consult
the provider's pgvector documentation before running migrations against it.

The migration is dialect-aware: on any non-PostgreSQL database (this codebase's SQLite test engine) it
is a deliberate no-op — the `embedding` column type (`app/db/vector_type.py::PortableVector`) already
keeps working as plain JSON there, since pgvector cannot exist on SQLite. `KnowledgeRetrievalService`
branches the same way at query time: a real `ORDER BY embedding <=> :query_vector` SQL query on
PostgreSQL, the original Python-side cosine-similarity ranking on SQLite — no second RAG system, one
codebase, two dialect-appropriate code paths.

Index type: HNSW (`vector_cosine_ops`), chosen over IVFFLAT because this table is populated
incrementally (a few chunks per tenant at a time via `KnowledgeRetrievalService.index_file`) rather than
bulk-loaded — HNSW needs no training/build-data pass and stays a valid index at any table size, where
IVFFLAT's `lists` parameter needs tuning to the eventual row count and degrades in quality before enough
rows exist.

No new external credential was introduced by this phase — the embedding *provider* (OpenAI, needing
`OPENAI_API_KEY`) is unchanged and was never required to build or verify this upgrade; all pgvector
verification used the existing `DeterministicEmbeddingProvider` test double sized to the real 1536
dimension (`DeterministicEmbeddingProvider(dimensions=1536)`), still explicitly TEST-ONLY and never
constructed that way by production code (`get_embedding_provider()`).

## Phase 11 addendum — SCHEDULE trigger dispatch closed, no new external credential

Closed the one documented gap from the prior Automation Engine phase: `TriggerType.SCHEDULE` now
actually dispatches. Reused the exact same in-process poll-tick mechanism Morning Brief's own
`check_and_generate_scheduled` already used (`app/events/worker.py`'s `on_tick` hook) — no second
scheduler, no new dependency (no Celery/APScheduler/cron daemon). A new `combine_on_tick` helper
composes both hooks onto that one slot, isolating each hook's own failures.

Added one new column, `Organization.timezone` (migration `0031`), the tenant's general business
timezone — distinct from the pre-existing `morning_brief_timezone`, which stays untouched. Verified via
real PostgreSQL (upgrade/downgrade/upgrade round-trip against `klaros_test`, single head `0031`).
Idempotency under genuine concurrent scheduler ticks was verified against real PostgreSQL, not SQLite
(`tests/test_postgres_schedule_concurrency.py`) — 10 concurrent ticks for the same due occurrence
produce exactly one execution, proven at the database level via the same real unique-constraint pattern
already used for EVENT-trigger idempotency.

Also closed the two worked examples deferred in the prior phase (Stale Quote, Overdue Invoice) — both
implemented as a real two-automation composition using only pre-existing domain sweeps
(`quotes.detect_expired_quotes`, `finance.detect_overdue_invoices`) and the real events they already
published (`quote.expired`, `exception.created`); no new `EventType` was added.

Live-verified in-browser end-to-end: created a real SCHEDULE-triggered automation, published it, and
watched the real in-process background worker (not a manual override) dispatch it automatically on its
next poll tick — the resulting notification and Owner Cockpit "Automations" widget both reflected real
database state.

## Automation Engine phase addendum — no new external credential required

The generic Automation Engine (`app/models/automation.py`, `app/services/automation_service.py`,
`app/services/automation_condition.py`, `app/api/v1/automations.py`, `app/events/automation_handlers.py`,
`app/workflows/automation_workflow.py`) introduces **zero new integration surface**: it reuses the
existing PostgreSQL connection (`DATABASE_URL`), the existing EventBus/Redis transport (`REDIS_URL`,
`EVENT_TRANSPORT`), and the existing Temporal connection (`TEMPORAL_HOST`, `TEMPORAL_TASK_QUEUE`) for its
one durable "wait" step — no new environment variable was added, so `.env.example` is unchanged this
phase.

Re-verified all three real local services this phase (they do not persist across sandbox restarts and
had to be restarted): PostgreSQL 16.6 (`pg_isready` → accepting connections), Redis 8.10.1 (`redis-cli
ping` → `PONG`), Temporal dev server (process confirmed running, `temporal server start-dev`). Ran the
Automation Engine's own migration (`0030_automation_engine.py`) against a real Postgres database
(`klaros_test`) — clean `alembic upgrade head`, single head confirmed. The one durable-wait Temporal
workflow (`AutomationWaitWorkflow`) was verified against `temporalio.testing.WorkflowEnvironment`'s real
local test server + a real `Worker`, not mocked (`tests/test_automation_wait_workflow.py`) — the same
pattern already established by `tests/test_temporal_workflows.py` for every other workflow in this
codebase. Idempotency under genuine concurrency was verified against real Postgres, not SQLite
(`tests/test_postgres_automation_concurrency.py`) — 10 concurrent duplicate-event racers produce exactly
one `AutomationExecution` row, proven at the database level.

`TriggerType.SCHEDULE` is accepted by the service's own validation (for forward-compatibility) but has no
dispatcher wired up yet — nothing polls for SCHEDULE-triggered automations. This is an honest, documented
gap, not a fabricated feature: the frontend automation editor deliberately omits SCHEDULE from its
trigger-type picker so it never offers a button that would silently do nothing.

## Phase 9 addendum — same credential boundary, re-confirmed

Re-ran the full environment audit: PostgreSQL/Redis/Temporal still reachable, Alembic still single head
`0029`, full SQLite regression unchanged (882 passed/11 skipped), Phase 7's two fixes re-verified intact
against real Postgres (14/14). All seven voice-provider credentials remain `NOT_CONFIGURED`
(re-checked, values never read into this document). Additionally traced the provider-selection wiring
end to end (`.env` → `Settings` → `get_streaming_stt_provider()`/`get_streaming_tts_provider()`/
`get_ai_provider()`): the default (`"auto"`, both in code and in this repo's own `.env`/`.env.example`)
only ever selects a real provider class when its matching API key is actually present, otherwise the
honest `NotConfigured*`/disconnected class — the literal string `"deterministic"` is never set outside
`tests/conftest.py`, so there is no path by which a production deployment could silently run the test
double. No code changes were made this phase — there was no boundary reachable to exercise. See the
Phase 9 certification for the full breakdown.

## Phase 8 addendum — real phone call certification attempt

Pre-flight for a real end-to-end phone call: PostgreSQL/Redis/Temporal confirmed still running (Phase 7
infrastructure survived, single Alembic head `0029`, full regression re-run clean: 882 passed/11
skipped on SQLite, the Phase 7 PostgreSQL fixes re-verified against real Postgres: 14/14 passed).
Credential check (presence only, values never read into this report): `TWILIO_ACCOUNT_SID`,
`TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, `DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`, `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY` are all **NOT_CONFIGURED**. Every provider-dependent objective of this phase (real
Twilio call, real Deepgram stream, real ElevenLabs stream, real AI provider call, and therefore the
real end-to-end phone call itself) is **BLOCKED BY CREDENTIAL** — no code changes were made, per this
phase's own explicit rule against fabricating providers or substituting deterministic doubles for a
PASS. See the Phase 8 certification for the full breakdown.

## Phase 7 addendum — real infrastructure verification

PostgreSQL, Redis, and Temporal moved from "code exists, never run against the real thing" to
**LIVE VERIFIED** this phase — real local installations (see DEPLOYMENT_RUNBOOK.md's Phase 7 section),
not Docker, not simulated. Twilio, Deepgram, ElevenLabs, and every LLM provider remain
**BLOCKED BY CREDENTIAL** — real infrastructure was provisioned, but no paid third-party account exists
for any of them in this environment, and none can be substituted by local installation. See
ARCHITECTURE_TRACEABILITY.md for the exact evidence behind each PASS.

This document is the source of truth for what is real, what is stubbed, and exactly what's
needed to move each provider from NOT_CONNECTED to CONNECTED. As of this writing, **no
provider has real credentials configured** in this development environment — every status
below reflects that. `GET /api/v1/integrations` (and the frontend's Settings → Integrations
page) always reflects live, real-time status; this document explains the *why* behind it.

## How status is determined

`app/integrations/adapters.py` holds one adapter class per provider. Every adapter's
`get_status()` reports `NOT_CONNECTED` if its required env var(s) are unset. Providers with a
real API client (Stripe, Twilio, SendGrid, Anthropic, OpenAI) additionally implement
`check_status()`, which makes one real, cheap, read-only API call to distinguish "credential is
set" from "credential actually works" — `CONNECTED` is only ever reported after that real call
succeeds. Providers without a real client yet (ServiceTitan, Jobber, Google Ads, Meta Ads, Gmail)
can only ever report `NOT_CONNECTED`/`ERROR`, honestly, since there is no real call to make.
**Phase 13**: QuickBooks moved out of that list — it now has a real OAuth2 client, a real
verifier, and a real invoice-sync capability; see the dedicated section below. **Phase 14**:
Google Calendar never had a platform-level adapter here at all — it went straight to the
tenant-scoped OAuth model below, alongside QuickBooks.

---

## Stripe — REAL, hardened Phase 12F, not yet connected with a real key (no credentials configured)

**Architecture**: `app/integrations/stripe_client.py` (`StripeClient`, direct httpx calls to
Stripe's REST API — no `stripe` SDK dependency added, matching this project's established
pattern of direct HTTP integration; see `app/services/ai_provider.py`). Configurable timeout/
retries (`STRIPE_TIMEOUT_SECONDS`/`STRIPE_MAX_RETRIES`, Phase 12F — previously hardcoded), real
retry-with-exponential-backoff on 429/5xx/timeout/network errors, real error classification
(`StripeErrorType`: `authentication`/`rate_limit`/`timeout`/`provider_error`/`network_error`/
`invalid_request` — 401/403 never retried, since retrying an invalid key wastes quota), real
webhook signature verification (`verify_webhook_signature`, HMAC-SHA256 per Stripe's documented
scheme, with replay-attack timestamp tolerance).

**Capabilities implemented**:
- `create_checkout_session` — a real, hosted Stripe Checkout payment link for an invoice, now
  with a real idempotency key (`klaros-checkout-{invoice_id}-{amount_due}`, Phase 12F) so a
  retried/duplicate request never creates two separate Checkout Sessions for the same invoice.
- `create_payment_intent` / `retrieve_payment_intent` — lower-level, not currently used by the
  checkout flow but available.
- `create_refund` — issues a real refund against a PaymentIntent, with an idempotency key.
- `verify_connection` — a real `GET /v1/balance` call, used by `check_status()` and by the new
  tenant-scoped connection verifier (below).

**Credential model (Phase 12F)**: Stripe can now be configured TWO ways, checked in this order
by `finance.create_stripe_checkout_session`:
1. **Tenant-scoped** (new) — a tenant connects their OWN Stripe secret key via
   `POST /api/v1/integrations/connections/stripe/connect` (or the Settings → Integrations →
   "Your Own Stripe Account" UI), stored encrypted in `integration_connections` (Phase 12D
   model) and verified with a real `GET /v1/balance` call the moment it's submitted
   (`app/api/tool_deps_integrations.py::_stripe_verifier`). Never falls back to another
   tenant's key — `IntegrationConnectionService.get_connection()` is itself tenant-scoped.
2. **Platform-level** (unchanged since Phase 12C) — `Settings.STRIPE_SECRET_KEY`, one shared key
   for the whole platform, used when a tenant hasn't connected their own account.

Webhook signature verification remains platform-level only (`Settings.STRIPE_WEBHOOK_SECRET`) —
a tenant-scoped webhook secret would require a real per-tenant webhook endpoint/URL scheme
(effectively Stripe Connect), which was deliberately not built this phase: there is no real
Stripe Connect application to build and verify it against. This is a known, honest limitation,
not an oversight — see "Not implemented" below.

**Flow**: `finance.create_stripe_checkout_session` (a real ToolRegistry tool, `AUTO` policy —
it only generates a payment *link*, no money moves yet, same reasoning as
`finance.send_invoice`) creates a Checkout Session with `tenant_id`/`invoice_id`/`customer_id`
stamped into the PaymentIntent's metadata. The customer pays on Stripe's own hosted page.
`POST /api/v1/webhooks/stripe` receives Stripe's webhook, verifies the signature, deduplicates
via the `webhook_events` table's `(provider, external_event_id)` unique constraint, reads
`tenant_id`/`invoice_id`/`customer_id` back out of the metadata (never trusted from anywhere
else), and dispatches by event type:
- `payment_intent.succeeded` → `PaymentService.record_payment()` (unchanged since Phase 12C) —
  idempotent via `(tenant_id, provider, external_id)` on `Payment`.
- `payment_intent.payment_failed` (**new Phase 12F**) → publishes the existing `PAYMENT_FAILED`
  event with the real decline reason; no `Payment` row is created (nothing succeeded).
- `charge.refunded` (**new Phase 12F**) → reconciles a refund issued OUTSIDE Klaros (Stripe
  Dashboard, a dispute, etc.) against the matching `Payment` via the new
  `PaymentService.reconcile_external_refund()`, using Stripe's own CUMULATIVE
  `amount_refunded` (not a delta) so a redelivered webhook is a safe no-op, never a double
  refund — proven by a dedicated duplicate-delivery test.

**Phase 15**: `payment_intent.succeeded` now also handles a second purpose — a quote deposit. A
Checkout Session created by `QuoteDepositService`/`finance.create_quote_deposit_checkout_session`
stamps `metadata.purpose = "quote_deposit"` and `metadata.quote_id` (instead of `invoice_id`); the
webhook branches on `purpose` before falling through to the unchanged invoice path above, verifies
the quote's real `tenant_id` matches the metadata's claimed one before recording anything, records
the `Payment` with `Payment.quote_id` set (no `Invoice`/`PaymentAllocation` involved — there is no
invoice yet at deposit time), and advances the quote `DEPOSIT_PENDING → DEPOSIT_PAID → CONVERTED`
(creating the real downstream `Job`) via `QuoteService.mark_deposit_paid`. Reuses `create_checkout_
session` (the SAME hosted-page flow as invoices) rather than `create_payment_intent` — no
Stripe.js/Elements needed on the frontend. See `PROJECT_STATUS.md`'s Phase 15 section for full
detail.

**Phase 16**: `/quotes/view/[id]` (the customer-facing page) now actually calls this checkout
endpoint and full-page-navigates the browser to the returned `checkout_url` — the first real
frontend caller of a Stripe-hosted Checkout flow anywhere in this codebase (the invoice-side
equivalent is staff-only, called through the authenticated `ToolRegistry`, with no frontend UI).
The page never trusts Stripe's `?deposit=success` return redirect as proof of payment — it polls
the real quote-status endpoint until the webhook-confirmed state actually lands. `node`/`npm`
absent from this sandbox — this frontend behavior is written and statically reviewed but not
typechecked/built/browser-verified; see `PROJECT_STATUS.md`'s Phase 16 section.

**Refunds — two paths, one shared DB state**:
- **Klaros-initiated** (unchanged since Phase 12C): `finance.create_refund_request` → always
  lands `REQUESTED` with a real `ApprovalRequest` → `finance.approve_refund` (human-only) →
  `PaymentService.decide_refund()` makes a real Stripe refund call (idempotency key
  `klaros-refund-{refund_id}`) *before* marking the refund `COMPLETED`; if the real call fails,
  a dedicated test (`test_stripe_refund_failure.py`) proves NO DB state changes at all.
- **Stripe-initiated** (new Phase 12F): `charge.refunded` webhook → `reconcile_external_refund`,
  above — creates its own `Refund` row (status `COMPLETED` directly, since Stripe already
  completed it) so both paths are visible in the same audit trail.

**A real, exploitable P0 bug found and fixed this phase** (not Stripe-specific in cause, but
found while hardening the Stripe refund path): `finance.approve_refund`/`reject_refund` (and,
found to share the identical gap, `finance.approve_invoice`/`reject_invoice`,
`finance.approve_credit_note`/`reject_credit_note`, `finance.approve_writeoff`/`reject_writeoff`)
relied SOLELY on role-based permission gating, with no explicit actor-type check — unlike the
generic `approval.approve_action`/`reject_action` tools, which have always had one.
`Role.MANAGER` — the only role `AIExecutionService` has ever actually been invoked with in this
codebase — genuinely holds every one of `APPROVE_REFUND`/`APPROVE_INVOICE`/
`APPROVE_CREDIT_NOTE`/`APPROVE_WRITEOFF`. A captured proof-of-concept (not a hypothetical)
showed an AI-actor context with that role could call `finance.approve_refund` directly through
the real `ToolRegistry` and have it **succeed**, completing a real refund with no human ever
involved. Fixed by adding the same explicit `ActorType.AI` guard the generic approval tools
already had, to all eight tools; proven closed by 8 new tests
(`tests/test_finance_approval_ai_guard.py`), including one that documents the human path still
works unchanged.

**Marketing attribution (Phase 12F Step 15)**: a real Stripe payment (via the real webhook)
flows into the SAME Marketing Attribution Loop every other payment source uses —
`PAYMENT_RECEIVED` → `marketing_handlers.handle_payment_received` →
`AttributionService.mark_paid()` → `CampaignConversion.collected_amount` → `campaign_performance()`'s
real ROAS/CAC calculation. Proven end-to-end by test, including that a duplicate webhook
delivery never double-counts collected revenue. **A real, non-obvious finding surfaced while
writing this test**: `EventBus.publish()` durably persists an event (the outbox pattern) but
does NOT synchronously invoke subscribers — attribution updates only land once something calls
`process_pending()` (the real `EventWorker`'s continuous poll loop, in production). This is
correct, pre-existing architecture, not a bug, but worth documenting: a webhook returning `200`
does not mean attribution has updated yet, only that the payment itself has (which IS
synchronous, inside `record_payment`).

**Required credentials**: `STRIPE_SECRET_KEY` (platform-level; a real Stripe secret key — test
mode `sk_test_...` is sufficient for verification) and/or a tenant connecting their own key via
the Integrations UI, plus `STRIPE_WEBHOOK_SECRET` (from the Stripe Dashboard's webhook endpoint
configuration, or the Stripe CLI's `stripe listen` for local testing) for the platform account.

**Environment variables**: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_TIMEOUT_SECONDS`
(default 20.0), `STRIPE_MAX_RETRIES` (default 3).

**Webhook setup**: point a Stripe webhook endpoint (Dashboard → Developers → Webhooks, or
`stripe listen --forward-to localhost:8000/api/v1/webhooks/stripe` for local dev) at
`POST /api/v1/webhooks/stripe`, subscribed to `payment_intent.succeeded`,
`payment_intent.payment_failed`, and `charge.refunded`.

**Testing procedure**:
1. Set `STRIPE_SECRET_KEY`/`STRIPE_WEBHOOK_SECRET` in `backend/.env` (test-mode key), or connect
   a tenant's own key via Settings → Integrations.
2. Restart the backend; `GET /api/v1/integrations` should show `stripe: CONNECTED` (platform) —
   the tenant-scoped connection is verified immediately on connect, no restart needed.
3. Call `finance.create_stripe_checkout_session` for a real invoice; open the returned
   `checkout_url` and pay with a Stripe test card (e.g. `4242 4242 4242 4242`).
4. Confirm the invoice's `amount_paid`/`status` update after the webhook fires (use
   `stripe listen` locally, or a public tunnel, to actually receive it).
5. Request and approve a refund on that payment; confirm a real refund appears in the Stripe
   Dashboard's test-mode payments.
6. Issue a refund directly from the Stripe Dashboard (not through Klaros) and confirm the
   `charge.refunded` webhook reconciles it into the same `Payment`/`Invoice` state.
7. Decline a test payment (e.g. card `4000 0000 0000 0002`) and confirm `payment_intent.payment_failed`
   is received and `PAYMENT_FAILED` is published.

**Current verification status**: extensively unit/integration-tested (41 new Phase 12F tests:
9 `StripeClient` retry/backoff/classification via `httpx.MockTransport`, 7 tenant-connection
credential resolution/isolation, 6 webhook failure/refund-reconciliation, 3 attribution flow,
8 the AI-approval-guard regression suite, 1 refund-API-failure-leaves-no-state-changed, plus 7
security-boundary gap tests added at phase close: no tool can ever record a `stripe`-sourced
payment (only the signed webhook can — `finance.record_test_payment` is pinned to the internal
test provider), a cross-tenant signed webhook cannot credit another tenant's invoice (recorded
FAILED, full rollback, no orphan `Payment`), checkout refuses to run with no credential and
surfaces a real Stripe API error as a `ToolError`, and `finance.record_test_payment` honors
the tenant automation policy (APPROVAL_REQUIRED records nothing, BLOCKED never executes, a
genuinely APPROVED request executes via `ApprovalExecutionService`)), plus the pre-existing
12/12 webhook signature/endpoint tests, all still passing. Full suites re-run at close: **398
passed, 8 skipped on SQLite**; **406 passed on real Postgres+Redis**. **Never exercised against
the real Stripe API with a real key** — no credentials configured, re-confirmed at the start and
end of this phase. `verify_connection()`'s real network call WAS exercised — with a fake key —
during live browser verification, and correctly, honestly reported `ERROR: secret_key rejected
by Stripe's API`, proving the full real HTTP pipe works; it has simply never been given a real
key to succeed with.

**Not implemented this phase**: per-tenant webhook signature verification (would require a real
Stripe Connect application — out of scope without one to build against); a full Stripe Connect
onboarding flow; `checkout.session.expired`/`charge.dispute.created` and other Stripe event
types beyond the three now handled.

**Phase 12G — self-contained audit (credentials still absent, re-confirmed; no live call made)**:
a full code-level pass over every file in the Stripe integration path (client, webhook receiver,
`PaymentService`, tenant-credential resolution, tools, models), verifying against the actual
code rather than assuming from the description above still held:
- Signature verification: real HMAC-SHA256 with constant-time compare and a 300s replay window;
  confirmed no dev-mode/debug flag anywhere skips it; a failed-signature event is deliberately
  never persisted to the idempotency ledger (so a forged event id can't poison future dedup).
- Idempotency: outbound calls (`create_checkout_session`, `create_refund`) both pass a real
  `Idempotency-Key` header; inbound webhooks dedupe via `webhook_events`'
  `UNIQUE(provider, external_event_id)`, with the check-then-insert race itself handled (a
  concurrent duplicate is caught as `IntegrityError` and treated as a benign no-op, not a 500).
  `PaymentService.record_payment` has its own independent `(tenant_id, provider, external_id)`
  uniqueness check as a second, redundant layer.
- Tenant isolation: `tenant_id` is only ever read from Stripe object metadata this app itself
  stamped when creating the Checkout Session — never from unauthenticated request data — and is
  cross-checked against the invoice's own `tenant_id` before any allocation; a cross-tenant forged
  metadata attempt is explicitly tested and fails closed.
- Duplicate delivery: proven a safe no-op for both a redelivered `payment_intent.succeeded` and a
  redelivered `charge.refunded` (the latter reconciles off Stripe's cumulative `amount_refunded`,
  not a delta, so replay can't double-refund).
- Retry/failure: `STRIPE_TIMEOUT_SECONDS`/`STRIPE_MAX_RETRIES` are genuinely read into
  `httpx.AsyncClient(timeout=...)` and a real retry loop with exponential backoff; 401/403/
  other-4xx are correctly never retried, 429/5xx/timeout/network errors are, each with dedicated
  tests.
- Audit logging: every webhook (including rejected ones) leaves a `WebhookEvent` row; every
  `PAYMENT_RECEIVED`/`PAYMENT_FAILED`/`PAYMENT_REFUNDED` event is independently caught by the
  generic bus-driven `AuditLog` handler; every Stripe-related tool call goes through
  `ToolRegistry`'s own audit path with `redact_input()` applied.
- Secret hygiene: no `STRIPE_SECRET_KEY`/API-key value found co-occurring with any `logger.*`
  call in any Stripe-adjacent file; `StripeClient.__repr__` deliberately never exposes the key;
  a dedicated existing test (`test_key_never_appears_in_error_message`) already covers this.
- **One real gap found and closed**: `decide_refund`'s guard against re-deciding an
  already-settled refund (`if refund.status != RefundStatus.REQUESTED: raise ...`) was only
  incidentally exercised by the Stripe-failure test, never directly for the ordinary (non-Stripe)
  path. Added `tests/test_refund_state_transition_guard.py` — proves a `COMPLETED` refund can't be
  re-approved or rejected afterward, a `REJECTED` refund can't be approved afterward, and that
  neither attempt moves any money or touches any row.
- **Two findings documented, not fixed, as known limitations** (no live credential needed to fix
  either, but neither is a defect worth changing speculatively): `RefundStatus.APPROVED` and
  `PaymentStatus.PENDING`/`FAILED` are real enum members with no code path in this codebase that
  ever assigns them — reserved/future states, not evidence of a broken two-step flow, since the
  only refund flow implemented (`REQUESTED` → `COMPLETED`/`REJECTED` directly) is fully tested as
  written. Stripe's inbound webhook JSON and outbound API responses are handled as bare `dict`s
  with manual `.get()` calls end-to-end rather than through a Pydantic schema boundary (the tool
  layer's own `CreateStripeCheckoutInput/Output` IS a proper Pydantic model — the gap is one layer
  lower, at the raw Stripe-shape boundary) — defensive today, but a structural change on Stripe's
  side would surface as a `KeyError`/`AttributeError` deep in a handler rather than at one
  validation point.
- Test coverage confirmed genuinely credential-independent: none of the 8 Stripe test files use
  `skipif`/`xfail` gated on `STRIPE_SECRET_KEY` — every one mocks `httpx` or fabricates a
  signed payload with a test-only webhook secret, so the "398/406 passing" figures below were
  never inflated by silently-skipped coverage.

Updated full-suite results after that test: SQLite 400 passed, 8 skipped (2 new, previously
398/8); real PostgreSQL 16.2 + real Redis: 408 passed, 0 failed.

**Phase 12G-2 — dedicated Stripe schema-hardening pass (same phase, credentials still absent,
re-confirmed; no live call made)**: closed the one remaining item from the audit above — Stripe's
webhook/API bodies were handled as bare `dict`s with manual `.get()` calls end-to-end. New
`app/integrations/stripe_schemas.py` adds Pydantic models for exactly the shapes this app reads/
writes: the webhook envelope (`id`/`type` required, `data.object` handled per-event-type),
`payment_intent`/`charge` payloads, and the `checkout.session`/`payment_intent`/`refund` API
responses. Every model is `extra="allow"` — a field Stripe adds later is preserved, never
rejected, so forward-compatibility was a design requirement, not an afterthought. Business-rule
validation (tenant ownership, UUID well-formedness, legal state transitions) is unchanged — the
schemas only replace the "is this shaped correctly" layer beneath it.

Two real, previously-undiscovered bugs were found and fixed while building this:
1. **A validly-signed-but-malformed webhook body could crash the endpoint.**
   `verify_webhook_signature` parsed JSON internally right after the HMAC check passed; a body
   that verified (real secret, real signature) but wasn't valid JSON raised an uncaught
   `json.JSONDecodeError` that propagated to an unhandled 500. Fixed with a dedicated
   `StripeWebhookPayloadError`, now mapped to a clean `400 malformed webhook payload` — proven by
   a new test that first confirms the raw client function raises the right exception, then
   confirms the HTTP endpoint returns 400 rather than crashing.
2. **A multi-field validation failure could overflow the `error_detail` column.** `WebhookEvent.
   error_detail` is `VARCHAR(500)`. A single Pydantic `ValidationError` naming several
   simultaneously-invalid fields on one `payment_intent`/`charge` object (e.g. missing `id` AND a
   wrong-typed `metadata` AND a wrong-typed `amount`, all at once) renders as a message that can
   exceed 500 characters (manually confirmed at 673 characters for a representative 3-error
   case) — invisible against SQLite, which never enforces `VARCHAR` length, but a genuine
   `StringDataRightTruncationError` against real Postgres. This is the exact same bug class the
   Phase 12B audit found for `communication_logs.status`. Fixed by truncating `error_detail` to
   the column's real limit at the one write site in `app/api/v1/webhooks.py`, with a comment
   pointing at the Phase 12B precedent. Proven by a dedicated test run against BOTH SQLite and
   real Postgres+Redis — the real-Postgres run is the one that actually exercises the
   VARCHAR(500) constraint this fix protects against, not just the Python-level truncation logic.

A third, smaller finding was fixed in passing while reviewing the new envelope-validation log
line for information hygiene: Pydantic's default `ValidationError.__str__()` echoes a repr of the
actual (possibly PII-bearing) input value for every failed field — logging `str(exc)` directly
would have duplicated webhook body content into structured logs beyond what the signed request
already legitimately carries. Changed to log only each failed field's location and error type.

15 new tests added (`tests/test_stripe_schema_hardening.py`) covering: malformed JSON (both the
raw client function and the HTTP endpoint), envelope validation (missing `id`/missing `type`,
both as unit tests and through the live endpoint, confirming a rejected envelope is never
persisted to the dedup table), unknown-field tolerance (both as a schema-level unit test and a
full webhook round-trip carrying fields this app has never seen), outbound response validation
(both a well-formed `checkout.session` response and one deliberately missing the `url` field
this app depends on — proving Stripe-side schema drift surfaces as a classified `StripeAPIError`,
never an unhandled `pydantic.ValidationError`), the `error_detail` truncation fix, and three new
cross-tenant refund tests (tenant B can neither approve nor reject tenant A's refund, via either
the tool layer or `PaymentService.decide_refund` directly — the guard already existed and was
already correct, just not previously pinned down as an explicit, isolated test independent of the
Stripe-failure test it was incidentally covered by). All 47 pre-existing Stripe tests continued
to pass completely unchanged throughout — this was a hardening pass on an already-largely-correct
implementation, not a rewrite. The one behavior-visible code change outside the client/schemas:
`create_checkout_session`'s return type changed from an untyped `dict` to
`StripeCheckoutSessionResponse`, so its one call site in `app/tools/builtin/stripe_tools.py`
moved from `session_obj["url"]` to `session_obj.url` (and `["id"]` → `.id`).

**Final counts, both re-run against the completed final code state** (SQLite: 415 passed, 8
skipped, 0 failed; real PostgreSQL 16.2 + real Redis: 423 passed, 0 failed, 0 skipped, 0 errors,
420.33s) — see `PRODUCTION_READINESS.md`'s Phase 12G-2 section for the full breakdown, including
the dedicated `tests/test_stripe_schema_hardening.py` file run in isolation (15/15 on both
backends) and the fresh migrations-from-empty re-verification (18/18, landing 93 tables, alembic
head `0018`, run against an isolated throwaway schema in the same real Postgres instance —
`public` and its existing data were never touched). Credential status unchanged: `STRIPE_SECRET_KEY`/
`STRIPE_WEBHOOK_SECRET` still empty in `backend/.env`; STEP 3's live-provider verification
remains BLOCKED BY CREDENTIAL, not attempted, not faked.

**Phase 22** performed a full adversarial production-readiness audit of the entire Stripe money
flow (deposit/Checkout/webhook/Payment/Job/Invoice/QuickBooks and the refund path) and found and
fixed one real, high-severity bug in `JobService.create_job`'s idempotency handling under
concurrent Stripe webhook delivery — see `PROJECT_STATUS.md`'s and `PRODUCTION_AUDIT.md`'s Phase
22 sections for full detail. **Phase 23** attempted to move from that self-contained verification
to live Stripe test-mode verification, per Phase 22's own recommendation, and independently
re-audited the whole lifecycle rather than assuming Phase 22's certification was correct.
`STRIPE_SECRET_KEY`/`STRIPE_WEBHOOK_SECRET`/QuickBooks credentials remain confirmed **absent** — no
live Stripe API call, live webhook delivery, or live provider-side idempotency observation was
possible, attempted, or claimed. The re-audit found a second, distinct real defect: a *sequential*
recovery gap (separate from Phase 22's *concurrent* race) where a transient, non-duplicate-key
failure inside `JobService.create_job` during the deposit webhook path left the customer's deposit
charged, the quote at `DEPOSIT_PAID`, and no Job ever created — permanently, since both the
`WebhookEvent` dedup check and `QuoteService.mark_deposit_paid`'s own idempotency check treated the
stuck state as final, blocking any redelivery (Stripe's own retry, or a manual Dashboard resend)
from ever completing it. Fixed in both places; re-verified a redelivery now converges to exactly
one Payment, one Job, `CONVERTED`. Full regression suite re-run on both SQLite and real
PostgreSQL+Redis after the fix, plus 3 repeated runs of the concurrency-sensitive test files against
real Postgres to confirm the fix doesn't disturb Phase 22's own concurrency guarantee — see
`PROJECT_STATUS.md`'s and `PRODUCTION_AUDIT.md`'s Phase 23 sections for full detail. A further audit
pass in the same phase found and fixed a second, distinct concurrency bug: `PaymentService.
request_refund`'s overcommitment guard read the Payment row with no lock, so concurrent refund
*requests* against the same payment (not approvals — distinct from Phase 21's already-fixed
concurrent-approval race) could each independently pass the "does this fit?" check — reproduced
reliably under `asyncio.gather` against real Postgres (5 concurrent $30 requests against a $100
payment all succeeded, $150 total). Fixed with `with_for_update=True` on the Payment row, mirroring
Phase 21's identical fix for invoice overpayment; re-verified stable across 8 repeated runs. A
further re-verification pass then closed one remaining test-coverage gap (no new defect): the
"QuickBooks succeeds, local persistence fails" scenario was already architecturally correct
(deterministic per-Payment `request_id` for Intuit-side dedup) but never directly
failure-injection tested — a new test now forces that exact failure and confirms the retry sends
the identical `request_id`, proving the LOCAL half of the guarantee directly. Real Stripe
verification remains blocked purely on credential availability, not on any code concern.

**Phase 24** performed a disciplined readiness audit of this exact provider boundary — explicitly
not a feature phase — re-tracing the current code rather than assuming prior certifications. Newly
checked and found correct: QuickBooks OAuth's CSRF/state protection (a real signed, 10-minute JWT
binding the callback to the tenant/user/provider that started the flow), OAuth scope and API base
URLs matching Intuit's documented values, QuickBooks amounts confirmed sent as plain decimal
dollars with no cents-conversion mistake, credential storage re-confirmed genuinely
Fernet-encrypted, and every `IntegrationConnection` lookup re-confirmed tenant-scoped. All
credentials (Stripe and QuickBooks) confirmed still absent. **No defect found — per the mission's
own instruction, zero code changes were made.** Full regression re-run fresh matched the pre-phase
baseline exactly on both engines, confirming no regression from a zero-change phase.

---

## Twilio (SMS) — REAL, implemented, not yet connected (no credentials configured)

**Architecture**: `app/communications/twilio_adapter.py` (`TwilioSMSAdapter`, implements the
existing `CommunicationProvider` abstraction — no second messaging framework). Direct httpx
calls to Twilio's REST API (Basic Auth). `app/integrations/twilio_client.py` implements
Twilio's documented webhook signature scheme (HMAC-SHA1 over the full request URL + sorted
form params, base64-encoded).

**Capabilities implemented**: real SMS send (`send_sms`), storing the real Twilio `MessageSid`
as `CommunicationLog.external_id`; a status-callback webhook
(`POST /api/v1/webhooks/twilio/status`) that updates that log row's status as Twilio reports
delivery progress (`queued` → `sent` → `delivered`/`failed`).

**Wired into**: `app/communications/factory.py::get_communication_provider()` — the ONE place
that decides which provider is active per channel. When `TWILIO_ACCOUNT_SID`,
`TWILIO_AUTH_TOKEN`, and `TWILIO_FROM_NUMBER` are all set, every existing caller of
`CommunicationProvider.send_sms` (collections, retention reminders, appointment/job
notifications, outbound/nurture — all pre-existing services, none modified beyond swapping
their hardcoded `InternalTestCommunicationAdapter` for the factory) automatically starts
sending real SMS. No email support (Twilio doesn't do email); `send_email` on this adapter
always returns `False` with an honest `FAILED_NO_EMAIL_PROVIDER` status, never a silent no-op
success.

**Required credentials**: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` (from the Twilio Console), a
real Twilio phone number (`TWILIO_FROM_NUMBER`, E.164 format).

**Environment variables**: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`.

**Webhook setup**: configure the Twilio phone number's "Status Callback URL" (Console → Phone
Numbers → your number → Messaging) to `POST /api/v1/webhooks/twilio/status`. Inbound-message
handling (a customer replying by SMS) is NOT implemented — this platform uses one shared
Twilio account for all tenants, and there is no per-tenant number pool yet to route an inbound
reply back to the correct tenant; this is a real, named architectural gap, not an oversight.

**Testing procedure**:
1. Set `TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN`/`TWILIO_FROM_NUMBER` in `backend/.env`.
2. Restart the backend; `GET /api/v1/integrations` should show `twilio: CONNECTED`.
3. Trigger any existing SMS-sending flow (e.g. an appointment reminder); confirm a real SMS
   arrives at a real phone, and `CommunicationLog.external_id` is populated with a real
   `MessageSid`.
4. Configure the status callback URL and confirm the log row's status updates.

**Current verification status**: the webhook signature algorithm is unit-tested against a
self-signed fixture (5/5 tests) and the full status-callback endpoint (signature validation,
dedup, `CommunicationLog` update) is tested end-to-end (5/5 tests). Note: Twilio does not
publish a real (auth-token, url, params) → signature reference triple, so these tests prove
internal consistency and correct algorithm implementation per Twilio's documented scheme, not
byte-for-byte parity against a real Twilio-signed fixture. **Never exercised against the real
Twilio API** — no credentials configured. Real SMS send, `check_status()`'s real account lookup,
and a live status-callback from an actual Twilio-sent message remain unverified.

---

## SendGrid (email) — REAL, implemented, not yet connected (no credentials configured)

**Architecture**: `app/communications/sendgrid_adapter.py` (`SendGridEmailAdapter`, implements
the existing `CommunicationProvider` abstraction). Direct httpx calls to SendGrid's v3 Mail
Send API.

**Capabilities implemented**: real email send, storing SendGrid's `X-Message-Id` response
header as `CommunicationLog.external_id`. No templates/variables (SendGrid Dynamic Templates)
— this app builds its own email bodies from `MessageTemplate` + real business data
(see `app/services/*` callers), sent as plain text via SendGrid's Mail Send API. No delivery-
status webhook (SendGrid's Event Webhook) implemented yet — a real, named gap; bounce/failure
handling currently only covers the synchronous send-time HTTP error, not asynchronous bounces
reported later.

**Wired into**: same `get_communication_provider()` factory as Twilio — every existing
`send_email` caller (invoice delivery, appointment confirmations, retention/review requests,
approved marketing campaigns) automatically uses real SendGrid once
`SENDGRID_API_KEY`/`SENDGRID_FROM_EMAIL` are both set. No SMS support; `send_sms` on this
adapter always returns `False` with an honest `FAILED_NO_SMS_PROVIDER` status.

**Required credentials**: `SENDGRID_API_KEY` (from the SendGrid dashboard), a verified sender
identity (`SENDGRID_FROM_EMAIL` — SendGrid rejects sends from an unverified address).

**Environment variables**: `SENDGRID_API_KEY`, `SENDGRID_FROM_EMAIL`.

**Testing procedure**:
1. Verify a sender identity in the SendGrid dashboard.
2. Set `SENDGRID_API_KEY`/`SENDGRID_FROM_EMAIL` in `backend/.env`.
3. Restart the backend; `GET /api/v1/integrations` should show `sendgrid: CONNECTED`.
4. Trigger any existing email-sending flow (e.g. `finance.send_invoice` on an APPROVED
   invoice); confirm a real email arrives, and `CommunicationLog.external_id` is populated.

**Current verification status**: implemented and unit-testable at the adapter level; not yet
separately unit-tested this phase (unlike Stripe/Twilio's webhook crypto, there is no pure-logic
seam here to test without a real API call — `check_status()`'s `GET /v3/user/account` IS the
verification). **Never exercised against the real SendGrid API** — no credentials configured.

---

## OpenAI / Anthropic — REAL, hardened Phase 12E, not yet connected (no credentials configured)

**Architecture**: `app/services/ai_provider.py` (`AnthropicAIProvider`/`OpenAIAIProvider`, real
httpx calls to each provider's Messages/Chat Completions API — no SDK dependency). This remains
the ONLY path by which an LLM's output ever reaches this application. Two entry points now
exist on `AIProvider`:
- `enrich_brief()` (Phase 8B/9B, unchanged) — deliberately narrow, returns prose only (a
  headline string, per-insight rephrased text) — never a number, entity id, or status. Every
  `entity_id` an LLM references is cross-checked against the real, already-computed insight
  list and dropped if it doesn't match.
- `generate_structured()` (Phase 12E) — a general-purpose real call returning an `AICallOutcome`
  (success/failure, classified error type, latency, token usage) for callers beyond the Morning
  Brief. Callers validate `raw_text` against their own Pydantic schema, exactly like
  `enrich_brief` already does internally — see `AIQualificationService` below.

**The AI provider boundary still has no path to tools, SQL, shell, or arbitrary HTTP —
`AIExecutionService` (Phase 9) is the only way autonomous AI reaches `ToolRegistry`, completely
separate from `AIProvider`. `generate_structured()`'s result is advisory data a caller validates
and returns; it is never itself a tool call.**

**Phase 12E hardening** (`app/services/ai_provider.py`, `app/core/config.py`):
- Timeout/retries/max-output-tokens moved from hardcoded constants to `Settings`
  (`OPENAI_TIMEOUT_SECONDS`/`OPENAI_MAX_RETRIES`/`OPENAI_MAX_OUTPUT_TOKENS`, symmetric
  `ANTHROPIC_*`) — previously always 20s/0 retries/1024 (Anthropic only; OpenAI had no cap at
  all), now configurable per environment.
- Real retry with exponential backoff (`0.5 * 2^attempt`) on retryable failures only — 429 rate
  limits, 5xx, timeouts, and network errors retry; 401/403 (auth) and 400 (bad request) never
  retry (retrying an invalid key wastes time and quota).
- Real error classification (`AIErrorType`: `authentication`/`rate_limit`/`timeout`/
  `provider_error`/`malformed_response`/`network_error`) — previously every failure collapsed
  to a bare `None`.
- Real token-usage extraction from both providers' response `usage` fields (`input_tokens`/
  `output_tokens`) — previously discarded entirely. `estimated_cost_usd` is deliberately never
  computed (see `ai_invocation_log_service.py`'s docstring) — neither provider returns a
  real-time price, and a hardcoded price table would silently go stale; usage is recorded, cost
  is honestly reported as unavailable, never fabricated.
- Defense-in-depth key redaction (`_redact_key`) — every error string that could reach a log,
  an API response, or an audit row is scrubbed for the literal API key first, even though
  `httpx`'s own exception messages never include request headers (verified directly).

**New in Phase 12C** (unchanged this phase): `app/integrations/adapters.py`'s
`AnthropicIntegrationAdapter`/`OpenAIIntegrationAdapter` — real status verification via a
minimal real API call, now also reporting the configured model in the status detail string
(e.g. `"verified via GET /v1/models — model=gpt-4o-mini"`) once `CONNECTED`.

**New in Phase 12E — AI-assisted lead qualification** (`app/services/ai_qualification_service.py`,
`crm.ai_qualify_lead_advisory` tool, `POST /api/v1/leads/{lead_id}/ai-qualify-advisory`): the
first real consumer of `generate_structured()` beyond the Morning Brief, and the first real
usage of `AIInvocationLog`. **ADVISORY ONLY** — never writes to the `Lead` record; the existing,
deterministic `crm.qualify_lead` (`app/services/scoring.py::score_lead`, unchanged, still the
only thing that ever sets `Lead.lead_score`/`qualification_status`) remains untouched and is
still what a human/approval flow would call to actually apply a decision. `AUTO` policy — safe
because this tool mutates nothing itself. Prompt input is deliberately narrow: no name/phone/
email (contact PII not needed to reason about qualification — proven by a dedicated test that
asserts none of it appears in the constructed prompt), and the lead's free-text `description`
is fenced as DATA (proven by a dedicated prompt-injection test) exactly like the Morning Brief's
existing pattern. Every real invocation — success or failure — is recorded in
`ai_invocation_logs` (migration `0018`, tenant-scoped, safe metadata only, never the raw
prompt/response or the API key).

**Required credentials**: `ANTHROPIC_API_KEY` and/or `OPENAI_API_KEY`.

**Environment variables**: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `AI_PROVIDER` (`auto` by
default — prefers Anthropic if both are set), `ANTHROPIC_MODEL`, `OPENAI_MODEL`,
`OPENAI_TIMEOUT_SECONDS`/`OPENAI_MAX_RETRIES`/`OPENAI_MAX_OUTPUT_TOKENS`, symmetric
`ANTHROPIC_*` (all new Phase 12E, all optional with the pre-existing defaults).

**Testing procedure**: set the key, restart, confirm `GET /api/v1/integrations` shows
`CONNECTED` with the configured model in the detail string. For the Morning Brief: generate one
and confirm `mode: AI` (not `DETERMINISTIC`), with a real `ai_provider`/`ai_model`/
`ai_generation_ms` recorded. For AI-assisted qualification: `POST
/api/v1/leads/{lead_id}/ai-qualify-advisory` on a real lead and confirm `available: true` with a
structured recommendation, then check `ai_invocation_logs` for a row with real `input_tokens`/
`output_tokens`.

**Current verification status**: `enrich_brief()`'s and `generate_structured()`'s prompt
construction, JSON parsing, schema validation, retry/backoff, error classification, token-usage
extraction, and fallback-to-deterministic behavior are all exercised in the test suite (31 new
Phase 12E tests: `test_ai_provider_hardening.py`, `test_ai_qualification_service.py`,
`test_ai_qualification_tool_registry.py`, `test_ai_qualify_lead_api.py`) against a mocked
network seam. Tenant isolation, prompt/PII isolation, and audit/usage persistence are proven by
dedicated tests, including one that resolves a lead by an authenticated tenant context and
confirms a cross-tenant attempt is rejected before the AI provider is ever called. **Never
exercised against the real Anthropic/OpenAI API in this environment** — no credentials
configured, re-confirmed at the start and end of this phase. The `check_status()` real-call
path, real retry/backoff against a real rate limit, real token-usage numbers, and a real AI
qualification recommendation all remain implemented but unverified until a key is set.

---

## S3-compatible object storage — stub only, real client not implemented

**Architecture**: `app/storage/factory.py::get_object_storage()` — if `OBJECT_STORAGE_ENDPOINT`
is unset, returns the real, working `LocalFilesystemStorageAdapter` (tenant-scoped directories,
25MB cap, MIME allowlist, path-traversal guard — genuinely used today for job photos/
documents/voice notes, not a mock). If `OBJECT_STORAGE_ENDPOINT` IS set, returns
`NotConnectedObjectStorageAdapter`, which raises on every call. **No S3 client was implemented
in Phase 12C** — this was deprioritized in favor of the providers with credentials available
this session (Stripe/Twilio/SendGrid/OpenAI/Anthropic). The existing local-disk adapter is a
real, production-usable storage backend for a single-instance deployment; it is not
horizontally-scalable or suitable for multi-instance production without a shared filesystem.

**What's needed to implement it for real**: an httpx-based (or `boto3`, if adding a dependency
is acceptable) S3-compatible client implementing `ObjectStorageProvider`'s `put`/`get`/`delete`,
wired into the same factory, gated on `OBJECT_STORAGE_ENDPOINT`/`OBJECT_STORAGE_BUCKET`/
`OBJECT_STORAGE_ACCESS_KEY`/`OBJECT_STORAGE_SECRET_KEY` (already-defined env vars, currently
unused by any real client).

**Current verification status**: NOT IMPLEMENTED. The local-disk fallback is real and tested;
the S3 path is an honest stub.

---

## QuickBooks Online — REAL OAuth2 client + invoice sync (Phase 13), not yet connected (no platform app credentials configured)

**Architecture**: `app/integrations/quickbooks_client.py` (`QuickBooksClient`, direct httpx calls
— no `intuit-oauth`/`python-quickbooks` SDK dependency added, matching this project's Stripe
pattern) + `app/integrations/quickbooks_schemas.py` (Pydantic response/request schemas,
`extra="allow"` — same schema-hardening approach Stripe got in Phase 12G-2, applied to a new
provider rather than reused code). Real OAuth2 authorization-code grant + refresh-token grant,
real error classification (`QuickBooksErrorType`, mirroring `StripeErrorType`), real
retry-with-backoff on 429/5xx/timeout/network, real bounded timeout
(`QUICKBOOKS_TIMEOUT_SECONDS`/`QUICKBOOKS_MAX_RETRIES`).

**Why OAuth, unlike Stripe**: QuickBooks has no platform-level shared credential — every tenant
has their OWN QuickBooks company (a "realm"). `QUICKBOOKS_CLIENT_ID`/`QUICKBOOKS_CLIENT_SECRET`
are the Klaros platform's own registered Intuit app credentials (used to talk to Intuit's OAuth
server on ANY tenant's behalf), not a data credential — there is no platform-level "connected"
state the way Stripe has one; every tenant must connect their own company individually.

**Connect flow**: `GET /api/v1/integrations/quickbooks/authorize` (authenticated, gated by
`MANAGE_INTEGRATIONS`) returns Intuit's real consent-page URL with a signed, short-lived
(10-minute) `state` token binding the flow back to the requesting tenant/user
(`app/core/security.py::create_oauth_state_token` — reuses the existing `JWT_SECRET`/signing
mechanism, no second credential system). The tenant's browser visits Intuit's page, authorizes,
and Intuit redirects to `GET /api/v1/integrations/quickbooks/callback` (deliberately
UNAUTHENTICATED, same trust model as `app/api/v1/webhooks.py` — Intuit's redirect carries no
JWT) with `code`/`state`/`realmId`. The callback verifies `state`, exchanges `code` for real
tokens via `QuickBooksClient.exchange_code_for_tokens`, and stores them through the existing,
generic `IntegrationConnectionService.connect()` — no parallel credential-storage path. A real
verifier (`_quickbooks_verifier`, `app/api/tool_deps_integrations.py`) is registered, making a
real `GET .../companyinfo/{realmId}` call the moment a connection is created or re-verified —
mirrors Stripe's `GET /v1/balance` verifier exactly.

**Invoice sync**: `finance.sync_invoice_to_quickbooks` (a real ToolRegistry tool, `AUTO` policy —
moves no money, idempotent by construction) pushes an approved/sent/paid Klaros invoice to the
tenant's QuickBooks company via `QuickBooksSyncService`. Creates a matching QBO Customer the
first time (storing the mapping on the new `customers.external_provider`/`external_id` columns,
migration `0019` — mirrors the pre-existing `Invoice.external_provider`/`external_id` columns
exactly, so a second invoice for the same Klaros customer reuses the QBO Customer rather than
creating a duplicate), then creates the QBO Invoice, storing its id on the Klaros invoice's own
`external_provider`/`external_id`. An already-synced invoice (`external_provider="quickbooks"`)
is a safe no-op — no second API call, ever, for the same invoice. A 401 on either the
create-customer or create-invoice call triggers exactly one token-refresh-and-retry (QuickBooks
access tokens expire in ~1 hour; refresh tokens ~100 days), never an unbounded retry loop.

**Tenant isolation**: every sync/verify/connect path resolves the connection via
`(tenant_id, provider)` — a tenant with no QuickBooks connection of their own can never sync
using another tenant's; a tenant's invoice can never be synced by another tenant's connection
(proven by dedicated tests, not just code inspection).

**Required credentials**: `QUICKBOOKS_CLIENT_ID`/`QUICKBOOKS_CLIENT_SECRET` (the platform's own
registered Intuit developer app) and `QUICKBOOKS_REDIRECT_URI` (must exactly match the URI
registered with Intuit) for ANY tenant to be able to start a connect flow at all; after that,
each tenant's own OAuth consent produces their own tokens — no further platform-level credential
is needed per tenant.

**Current verification status**: extensively self-contained tested (31 new tests,
`tests/test_quickbooks_integration.py`, run independently: **31/31 passed**) — OAuth URL
construction, signed state-token round-trip/rejection (wrong provider, garbage token, and a
genuinely expired token constructed directly to prove the 10-minute window is really enforced),
`QuickBooksClient` retry/backoff/error-classification via real `httpx.MockTransport` (mirroring
`test_stripe_client.py`), the full OAuth callback HTTP flow (missing params, invalid state,
wrong-provider state, Intuit `error` param, a mocked-but-otherwise-real successful connect, a
mocked token-exchange failure), the verifier (success/missing-fields/rejection), and the full
sync service (not-connected, DRAFT-invoice rejection, already-synced no-op, full
customer+invoice creation, customer-reuse across two invoices, 401-triggers-refresh-then-retry-
once, cross-tenant invoice rejection, cross-tenant connection non-use) plus tool-layer wiring
through the real `ToolRegistry`. Full backend suite re-run against the completed final code
state: SQLite 446 passed/8 skipped/0 failed; real PostgreSQL 16.2 + real Redis 454 passed/0
failed/0 skipped/0 errors (472.40s). **Never exercised
against the real Intuit API** — no platform app credentials configured in this environment,
confirmed at the start of this phase; `QUICKBOOKS_CLIENT_ID`/`QUICKBOOKS_CLIENT_SECRET`/
`QUICKBOOKS_REDIRECT_URI` all empty in `backend/.env`.

**Not implemented as of Phase 13**: payment/refund sync (invoice-only this pass, a deliberately
bounded scope rather than a half-finished everything — **deposit-payment sync was added in Phase
17 and refund sync in Phase 18, see below**); a webhook receiver for QuickBooks' own entity-change
notifications (the sync direction remains Klaros → QuickBooks only, one-way push — no need for
QuickBooks to notify Klaros of anything yet); QBO Item-catalog integration (every invoice line
posts against a placeholder `ItemRef` rather than a real QBO Item, since building real Item sync
was out of this phase's bounded scope — QuickBooks accepts this without error, but a real
deployment would want real Item references for proper categorization).

## QuickBooks deposit-payment sync (Phase 17) — real, self-contained tested; BLOCKED BY CREDENTIAL for live verification

Closes the gap named above: a real Stripe quote-deposit `Payment` (Phase 15/16) can now be pushed
to QuickBooks as a real Payment applied against that job's invoice — the deposit `Payment` itself
previously had no QuickBooks representation at all.

**Design**: at the moment a deposit is paid, there is USUALLY no Klaros `Invoice` yet — only a
`Job` (Quote conversion, Phase 15, creates a Job, not an Invoice; invoicing remains a separate,
staff-triggered step via `finance.trigger_invoice_from_job`). Rather than invent a QuickBooks
SalesReceipt (which would misrepresent a deposit as a completed sale) or an unapplied payment,
`QuickBooksPaymentSyncService.sync_deposit_payment` treats "resolve the job's already-QuickBooks-
synced Invoice" as a genuine, honestly-reported precondition — it fails with a specific,
distinguishable error (`InvoiceNotYetCreatedError`/`InvoiceNotYetSyncedError`) rather than
silently skipping or fabricating a workaround.

**New client capability**: `QuickBooksClient.create_payment` (`Line[].LinkedTxn` applies the
payment to the QBO Invoice — the standard "receive payment" shape) and `get_payment` (read-only
retrieval), same retry/backoff/error-classification/bounded-timeout shape as every other client
method. `create_payment` passes Intuit's documented `?requestid=` write-deduplication query param
(a deterministic value derived from the Klaros `Payment.id`) — this has NOT been verified against
a real QuickBooks account (no credentials), implemented per Intuit's documented API contract only.

**Identifier storage**: a new `Payment.quickbooks_payment_id` column (migration `0023`) — NOT a
reuse of `Payment.provider`/`external_id`, which already identify the payment's ORIGINATING
provider (Stripe); this is a separate, secondary accounting-sync target slot, mirroring
`Invoice.external_provider`/`external_id`'s existing role exactly. Its presence is this app's own
local idempotency boundary; a Payment that already carries one is a safe no-op.

**Integration point**: `EventType.QUOTE_DEPOSIT_PAID` (already existed, Phase 15) now has a second
subscriber, `finance_quickbooks_deposit_payment_sync`, attempting the sync automatically —
reusing the `EventBus`'s own existing bounded retry + dead-letter queue (no second background
mechanism introduced). This is expected to fail on most first attempts (no invoice created yet)
and lands in the dead-letter queue, which is the "visibly ERROR/PENDING_RETRY, safely retryable
later" state — `EventBus.replay()` or the manual `finance.sync_deposit_payment_to_quickbooks` tool
(reusing `SEND_INVOICE` permission, `AUTO` policy) completes it once the invoice exists and has
itself been synced. **A QuickBooks sync failure never touches `Payment`/`Quote` state** — both are
already committed before the triggering event is even published (the existing outbox pattern).

**Tenant isolation & amount integrity**: `sync_deposit_payment(tenant_id, payment_id)` accepts only
those two arguments — every accounting value (amount, QBO customer id, QBO invoice id) is read
from Klaros' own persisted, tenant-scoped records, never from a caller; a cross-tenant lookup fails
before any QuickBooks API call (proven by dedicated tests, not just code inspection, matching the
established pattern from invoice sync/Google Calendar sync).

**Tests**: 30 new (`tests/test_quickbooks_deposit_payment_sync.py`, stable across 3 repeated runs).
See `PROJECT_STATUS.md`'s Phase 17 section for the full breakdown and exact final counts.

**Required credentials**: same as invoice sync above — `QUICKBOOKS_CLIENT_ID`/
`QUICKBOOKS_CLIENT_SECRET`/`QUICKBOOKS_REDIRECT_URI`, confirmed absent from `backend/.env` at the
start of this phase. Real-provider verification (a real QBO Payment landing in a real QuickBooks
company) is `BLOCKED BY CREDENTIAL`, not attempted, not faked.

## QuickBooks refund sync (Phase 18) — real, self-contained tested; BLOCKED BY CREDENTIAL for live verification

Closes the last unsynced piece: a real, completed Klaros `Refund` can now be pushed to a tenant's
connected QuickBooks Online company as a `RefundReceipt` applied against the original `Payment` it
reverses.

**Design**: determined from the existing accounting model, not guessed. A `RefundReceipt` —
QuickBooks' own documented object for "money already received, now being refunded back" — was
chosen over a `CreditMemo` (an unapplied credit toward FUTURE purchases, the wrong model for money
that has genuinely left the business via Stripe) and over voiding/editing the original `Payment`
(would destroy the historical record and can't represent Klaros' own PARTIAL-refund capability,
since `Refund.amount` can be less than `Payment.amount`).

**New client capability**: `QuickBooksClient.create_refund_receipt`/`get_refund_receipt` —
`Line[].LinkedTxn` (`TxnType: "Payment"`) ties the RefundReceipt back to the original QBO Payment,
mirroring how a QBO Payment ties to its Invoice. Same `?requestid=` write-deduplication parameter
as Payment sync — not independently verified against a real QuickBooks account (no credentials).

**Identifier storage**: `Refund.quickbooks_refund_receipt_id` (migration `0024`) — Refund had no
external-identifier column of any kind before this phase.

**Integration point**: `EventType.PAYMENT_REFUNDED` (already existed — fires at exactly refund
completion for BOTH the Klaros-initiated `decide_refund` path and the Stripe-Dashboard-initiated
`reconcile_external_refund` path, so no new EventType was needed) now has a second subscriber,
`finance_quickbooks_refund_sync`, reusing the `EventBus`'s own bounded retry + dead-letter queue.

**Precondition, inherited and documented not silently expanded**: the ORIGINAL `Payment` must
already carry a `quickbooks_payment_id` before its refund can sync — a refund can't reference a
QuickBooks Payment that was never created. At the time this phase was written, only Stripe
quote-deposit payments had a sync path to reach that state — **as of Phase 19, ordinary invoice
payments do too, and as of Phase 20, that includes payments split across multiple invoices** (see
below) — so a refund against any synced payment can now sync once its payment has been.

**A real bug found and fixed this phase**: Phase 17's `sync_deposit_payment` rejected any Payment
whose status wasn't exactly `SUCCEEDED`, meaning a payment that had SINCE been refunded could never
be synced to QuickBooks at all — permanently blocked, not merely delayed. Fixed by widening the
eligibility check to also accept `PARTIALLY_REFUNDED`/`REFUNDED` (only `PENDING`/`FAILED` — a
payment that never actually succeeded — are still rejected). All existing Phase 17 tests still pass.

**Tests**: 30 new (`tests/test_quickbooks_refund_sync.py`, stable across 3 repeated runs). See
`PROJECT_STATUS.md`'s Phase 18 section for the full breakdown and exact final counts.

**Required credentials**: same as invoice/payment sync above. Real-provider verification is
`BLOCKED BY CREDENTIAL`, not attempted, not faked.

## QuickBooks ordinary invoice payment sync (Phase 19) — real, self-contained tested; BLOCKED BY CREDENTIAL for live verification

Extends Phase 17's payment sync (previously deposit-only) to cover ORDINARY invoice payments too.

**Eligibility rule**: `Payment.quote_id is None` (not a deposit — those still use
`sync_deposit_payment`, unchanged) AND `provider == "stripe"` AND status is `SUCCEEDED`/
`PARTIALLY_REFUNDED`/`REFUNDED`. Originally restricted to exactly one `PaymentAllocation.
invoice_id` — **as of Phase 20, a payment split across multiple invoices is supported too**, see
below.

**Implementation**: `QuickBooksPaymentSyncService.sync_invoice_payment_to_quickbooks(tenant_id,
payment_id)` — the new public method on the SAME service Phase 17 built (no parallel service). The
shared "resolve connection, create the QuickBooks Payment with 401-refresh-retry, persist
`quickbooks_payment_id`" logic was factored into a private `_create_and_persist_payment` helper
used by both this method and the unchanged `sync_deposit_payment`, so no QuickBooks HTTP/retry
logic is duplicated between the two paths. Uses its own deterministic `requestid` namespace
(`klaros-invoice-payment-{id}`) — not independently verified against a real QuickBooks account.

**Event integration**: reuses the ALREADY-EXISTING `EventType.PAYMENT_RECEIVED` (fired for every
payment, deposit or ordinary) — no new EventType. The new subscriber silently no-ops for a deposit
payment or a non-Stripe payment (proven by a dedicated test), and reuses the `EventBus`'s bounded
retry + dead-letter queue for the genuine failure case (invoice not yet synced).

**Refund compatibility**: `QuickBooksRefundSyncService` (Phase 18) needed zero code changes — it
only ever checks `Payment.quickbooks_payment_id`, never how it got set — so a refund against a
now-synced ordinary invoice payment already works through the existing, unmodified refund service.
Proven end-to-end by a dedicated test.

**Tests**: 25 new (`tests/test_quickbooks_invoice_payment_sync.py`, stable across 3 repeated runs).
See `PROJECT_STATUS.md`'s Phase 19 section for the full breakdown and exact final counts.

**Required credentials**: same as invoice/payment/refund sync above. Real-provider verification is
`BLOCKED BY CREDENTIAL`, not attempted, not faked.

## QuickBooks split-payment sync (Phase 20) — real, self-contained tested; BLOCKED BY CREDENTIAL for live verification

Removes Phase 19's "one payment, one invoice" restriction after an explicit accounting-lifecycle
audit confirmed the underlying data model already genuinely supports one Klaros `Payment` split
across multiple `Invoice`s (`PaymentService.record_payment`'s `allocations` parameter is already a
list, each independently validated and reachable via `finance.record_test_payment`) — a real,
reachable shape, not a theoretical one, so the limitation was removed rather than left in place.

**Client**: `QuickBooksClient.create_payment` now accepts `invoice_lines: list[tuple[str, float]]`
(previously a single `invoice_id`/`amount` pair) — one real QBO `Line` entry per allocation,
`TotalAmt` as their sum. `sync_deposit_payment` (Phase 17, always exactly one invoice) is
unaffected — it now passes a single-item list to the same underlying call.

**Service**: `sync_invoice_payment_to_quickbooks` resolves EVERY `PaymentAllocation` for the
payment, requiring each allocated invoice to independently already be synced to QuickBooks — if
any one of several isn't, the error names which invoice. A new precondition was identified and
enforced: a single QBO Payment has exactly one `CustomerRef`, so if a payment's allocated invoices
belonged to different QuickBooks customers, syncing fails cleanly
(`AllocationSpansMultipleCustomersError`) rather than silently picking one — not reachable through
any real Klaros code path today, a defensive guard rather than an active gap.

**A real, independently-discovered bug fixed this phase**: `PaymentService.decide_refund` compared
THIS refund's own amount against `Payment.amount` to decide `REFUNDED` vs. `PARTIALLY_REFUNDED`,
not the cumulative total refunded — a payment fully refunded via several partial refunds never
reached `REFUNDED`. Fixed to match `reconcile_external_refund`'s already-correct cumulative
comparison. See `PROJECT_STATUS.md`'s Phase 20 section for full detail.

**Tests**: 2 net new in `tests/test_quickbooks_invoice_payment_sync.py` (a rejection test replaced
with three: successful split-payment sync, one-of-several-invoices-unsynced, different-customers
rejection) plus 5 new in `tests/test_payment_refund_status_cumulative.py`. All 87 existing Phase
17/18/19 QuickBooks tests re-verified passing after the client signature change.

**Required credentials**: same as invoice/payment/refund sync above. Real-provider verification is
`BLOCKED BY CREDENTIAL`, not attempted, not faked.

## Accounting production-readiness audit (Phase 21)

A full audit of the Phase 17–20 accounting lifecycle, explicitly not trusting prior certifications.
Found and fixed three genuine bugs by direct reproduction (not inference): duplicate-invoice-
allocation overpayment within a single payment (`amount_due` went to -$20), concurrent payments to
the same invoice across two separate calls (verified specifically against real PostgreSQL —
`with_for_update=True` row lock; SQLite has no equivalent), and concurrent refund approval calling
Stripe's real refund API twice for one refund (fixed with a CAS claim on `RefundStatus.APPROVED`,
previously a defined-but-unused status, executed before any Stripe call is even considered). Also
fixed a related QuickBooks duplicate-`Line` gap the first bug's fix newly made reachable. See
`PROJECT_STATUS.md`'s and `PRODUCTION_AUDIT.md`'s Phase 21 sections for full detail, including
exactly what was re-verified and found already correct.

## Google Calendar — REAL OAuth2 client + appointment sync (Phase 14), not yet connected (no platform app credentials configured)

**Architecture**: `app/integrations/google_calendar_client.py` (`GoogleCalendarClient`, direct
httpx — no `google-api-python-client`/`google-auth` SDK dependency added, matching this
project's Stripe/QuickBooks pattern) + `app/integrations/google_calendar_schemas.py` (Pydantic,
`extra="allow"`, built from the start with the schema-hardening pattern rather than needing a
later pass). Real OAuth2 authorization-code + refresh-token grants (`access_type=offline&
prompt=consent` on the consent URL, so a `refresh_token` is guaranteed even on a repeat
connect), real error classification (`GoogleCalendarErrorType`), real retry-with-backoff, real
bounded timeout (`GOOGLE_CALENDAR_TIMEOUT_SECONDS`/`GOOGLE_CALENDAR_MAX_RETRIES`).

**Deliberately additive, not a replacement**: the existing `CalendarProvider`/
`InternalTestCalendarAdapter` abstraction (`app/calendar/`) remains the source of truth for
availability/double-booking prevention within Klaros — this integration is a one-way push of an
already-decided Klaros `Appointment` to a tenant's own connected Google Calendar, the same
"additive sync, not a replacement for the internal system of record" relationship
`QuickBooksSyncService` already has with internal invoicing.

**Connect flow**: `GET /api/v1/integrations/google-calendar/authorize` (authenticated, gated by
`MANAGE_INTEGRATIONS`) returns Google's real consent-page URL with a signed `state` token
(reuses `create_oauth_state_token` — the exact same primitive QuickBooks uses, not a second
one). `GET /api/v1/integrations/google-calendar/callback` (deliberately unauthenticated, same
trust model as the QuickBooks/Stripe/webhook endpoints) verifies `state`, exchanges `code` for
real tokens, and stores them through the existing `IntegrationConnectionService.connect()` — if
Google omits a `refresh_token` (shouldn't happen given the consent-URL params above, but
checked), the callback fails honestly rather than storing a connection that would silently stop
working after the access token's ~1 hour lifetime. A real verifier (`_google_calendar_verifier`)
makes a real `GET /calendars/primary` call, mirroring the Stripe/QuickBooks verifiers exactly.

**Appointment sync**: `calendar.sync_appointment_to_google` (a real `ToolRegistry` tool, `AUTO`
policy — moves no money, reversible/idempotent) is one idempotent entry point covering
create/update/cancel: creates the Google event the first time, updates it on later calls once
`Appointment.external_id` is already set (new `external_provider`/`external_id` columns,
migration `0021`, mirroring the `Invoice`/`Customer`/`Quote` pattern exactly), and deletes/
cancels the Google event once the Klaros appointment itself is `CANCELLED` — a delete of an
already-gone event (a 404 from Google) is treated as a safe no-op, not a failure, and cancelling
an appointment that was never synced is also a safe no-op (never calls Google at all). A 401 on
any call triggers exactly one token-refresh-and-retry. Also exposed: `calendar.
list_google_calendars` and `calendar.check_google_availability` (a real `freeBusy` query) — both
reuse the same tenant-scoped credential resolution and refresh-on-401 handling.

**Deliberately a manually-invoked tool, not an automatic event-subscriber** on
`APPOINTMENT_CREATED`/`APPOINTMENT_UPDATED` — same reasoning `finance.sync_invoice_to_
quickbooks` already established: an external push should be an explicit, auditable action a
human or AI actor took, not a side effect that could silently retry against a flaky external API
on every internal event. The operation is idempotent regardless, so nothing is lost by it being
explicit rather than automatic.

**Required credentials**: `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` (the platform's own
registered Google Cloud OAuth client — a separate app from `GOOGLE_ADS_*`/`GOOGLE_BUSINESS_*`,
matching this project's one-app-per-integration convention) and `GOOGLE_REDIRECT_URI` (must
exactly match the URI registered with Google) for ANY tenant to be able to start a connect flow
at all; after that, each tenant's own OAuth consent produces their own tokens.

**Current verification status**: extensively self-contained tested (39 new tests,
`tests/test_google_calendar_integration.py`, run independently: **39/39 passed**) — OAuth URL
construction (including the `access_type=offline`/`prompt=consent` params), signed state-token
round-trip/rejection (wrong provider, garbage token, genuinely expired token), `GoogleCalendarClient`
retry/backoff/error-classification via real `httpx.MockTransport`, the full OAuth callback HTTP
flow (missing params, invalid/wrong-provider state, Google's own `error` param, a missing-
refresh-token rejection, a mocked-but-otherwise-real successful connect), the verifier
(success/missing-fields/rejection), and the full sync service (not-connected, unknown-appointment
error, full event creation with external-id persistence, update-instead-of-recreate on a second
call, cancellation deleting the Google event, cancelling-a-never-synced-appointment as a no-op,
deleting-an-already-gone-event as idempotent not a failure, 401-triggers-refresh-then-retry-once
on both event sync and calendar listing, and two cross-tenant-isolation tests) plus tool-layer
wiring through the real `ToolRegistry`. Full backend suite re-run: SQLite 508 passed/8 skipped/0
failed; real PostgreSQL 16.2 + real Redis 516 passed/0 failed/0 skipped/0 errors (518.60s).
**Never exercised against the real Google API** — no platform app credentials configured in this
environment, confirmed at the start of this phase; `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`/
`GOOGLE_REDIRECT_URI` all absent from `backend/.env`.

**Not implemented this phase**: two-way sync (a change made directly in Google Calendar is never
pulled back into Klaros — this is Klaros → Google only, one-way push, same directionality as the
QuickBooks invoice sync); a webhook/push-notification receiver for Google's own calendar-change
notifications; calendar selection persistence (every sync call defaults to `calendar_id=
"primary"`, passed explicitly if a tenant wants a non-primary calendar, but nothing remembers a
tenant's preferred calendar across calls — a natural small follow-up, not a correctness gap
today since `"primary"` is what most tenants would want anyway).

## Marketplace lead ingestion (Angi / Thumbtack / Nextdoor) — REAL, PROVIDER BLOCKED by design

`app/integrations/marketplace_adapters.py` + `app/api/v1/marketplace_webhooks.py`
(`POST /webhooks/marketplace/{provider}/{tenant_id}`). Researched before building: none of these
three publishes a public, self-serve webhook API or documented payload schema — Angi's own
integration process is manual partner onboarding to an unpublished "fixed" JSON schema (commonly
bridged via Zapier); Thumbtack and Nextdoor have no public developer docs at all. Building a parser
for any of their real, private schemas would be fabricating an external contract this project
forbids fabricating.

What's real instead: Klaros defines its OWN receiving contract — a per-tenant HMAC-SHA256 shared
secret (`X-Klaros-Signature` header) plus a per-tenant configurable field map (dotted JSON paths →
canonical lead fields, with sensible generic defaults per provider). A tenant connects one via the
existing generic `POST /integrations/connections/{provider}/connect` (same mechanism as
Stripe/QuickBooks/Google Calendar), then points their own Zapier "Webhooks by Zapier" action or the
marketplace's own webhook-config screen (Angi has one, per-account) at Klaros' URL with that secret.
No verifier is registered for `connect()` — there is no live API to call — matching the exact
existing honest precedent below for Gmail/Google Ads/Meta Ads: the connection's `status` column
never claims CONNECTED, but the stored secret is real and does gate every inbound request via a
real HMAC comparison. 16 tests (`test_marketplace_adapters.py`, `test_marketplace_lead_webhooks.py`)
cover normalization (default + custom field maps, all three providers), signature rejection,
tenant isolation, idempotent dedup, and unconfigured/unknown-provider responses.

## Knowledge-layer embeddings / RAG — REAL software, PROVIDER + INFRASTRUCTURE BLOCKED for live verification

`app/services/embedding_provider.py`, `app/services/knowledge_retrieval_service.py`,
`app/services/knowledge_qa_service.py`. Real chunking (deterministic, word-boundary, configurable
size/overlap), a real embedding-provider abstraction (`OpenAIEmbeddingProvider` — genuine
`POST https://api.openai.com/v1/embeddings` call, same retry/timeout/error-handling shape as
`app/services/ai_provider.py`), and real tenant-isolated cosine-similarity search (tenant filtering
enforced in the SQL `WHERE` clause, never fetch-then-filter).

Two honest limitations, not fabricated around:

1. **No `OPENAI_API_KEY` configured** — `get_embedding_provider()` returns
   `NotConfiguredEmbeddingProvider`, and every search/ask call reports `NOT_CONFIGURED` rather than
   silently falling back to a lower-quality substitute. Confirmed via real browser E2E this session
   (registered a tenant, edited a knowledge file, searched — got the honest "no embedding provider
   configured" message, not a fabricated result).
2. **No reachable PostgreSQL/`pgvector`** — the `ankane/pgvector` image is provisioned in
   `docker-compose.yml` but was never actually integrated. `KnowledgeChunk.embedding` is a portable
   JSON float array (works identically on SQLite and PostgreSQL); similarity ranking runs in Python
   over rows already scoped to the caller's tenant by the SQL query. This is real, tested, and
   functionally correct at small-to-moderate scale — it is NOT a `pgvector` ANN index, and this file
   makes no claim that it is.

A `DeterministicEmbeddingProvider` (bag-of-words, no external call) exists for tests ONLY —
`tests/conftest.py` sets `EMBEDDING_PROVIDER=deterministic` for the whole suite so retrieval/ranking
logic is exercised for real without an API key. It is never the production default. Confirmed via
the same browser session: setting `EMBEDDING_PROVIDER=deterministic` and restarting made search
return real, correctly-ranked results (`office/pricing-rules.md` scored 0.515 for a query about
plumbing rates, versus an unrelated file at a much lower score) — proving the retrieval pipeline
itself works end to end, with the deterministic provider's quality limitations clearly documented in
its own docstring (exact-word matching only, no real semantic understanding).

No Anthropic embedding provider exists — Anthropic has no native embeddings API (their docs point
users to Voyage AI); adding a fake one would be exactly the kind of fabrication this document exists
to prevent.

## AI Voice Receptionist (Deepgram / ElevenLabs / Twilio Media Streams) — REAL software, PROVIDER BLOCKED

`app/services/speech_provider.py`, `app/api/v1/voice_stream.py`, `app/services/voice_conversation_service.py`.
Reuses the existing Twilio signature verification (`app/integrations/twilio_client.py`) and the existing
inbound-voice webhook (extended, not duplicated) — when a tenant enables the receptionist
(`/settings/voice`), that webhook now returns real `<Connect><Stream>` TwiML pointing at a real
WebSocket endpoint implementing Twilio's actual, publicly-documented Media Streams protocol
(connected/start/media/stop/mark events).

Two real provider adapters, each implementing that provider's actual REST API request/response/error
shape:
- **Deepgram** (`DeepgramSTTProvider`) — real `POST https://api.deepgram.com/v1/listen`.
- **ElevenLabs** (`ElevenLabsTTSProvider`) — real `POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}`.

Neither has ever been called with a real key in this sandbox — `DEEPGRAM_API_KEY`/`ELEVENLABS_API_KEY`
are unset, so `get_stt_provider()`/`get_tts_provider()` return `NotConfiguredSTTProvider`/
`NotConfiguredTTSProvider`, and a real inbound call honestly ends in a human handoff rather than a
fabricated transcript or synthesized reply. A `Deterministic*Provider` pair exists for tests ONLY
(`tests/conftest.py` sets `STT_PROVIDER=deterministic`/`TTS_PROVIDER=deterministic`) — never the
production default.

Deliberately NOT implemented: live bidirectional streaming sessions to Deepgram/ElevenLabs (their
real-time WebSocket APIs, as opposed to the REST/batch endpoints above). This sandbox has no real
Twilio call ever carrying real caller audio to stream anywhere, and building an unverifiable streaming
protocol against it would itself be a form of fabrication. The REST/batch shape is real, tested, and a
legitimate first integration depth; upgrading to true low-latency streaming is a clearly-scoped future
change.

The conversation engine itself (`app/services/voice_conversation_service.py`) is fully real and tested
independent of audio: it takes transcript text in, produces a reply + governed actions out, reusing
`AIExecutionService` (the same AI→ToolRegistry boundary Morning Brief's AI mode already uses) for every
mutation and `KnowledgeQAService` (Phase 3) for grounded answers. 56 new tests in Phase 4 cover the core
loop — see ARCHITECTURE_TRACEABILITY.md.

**Phase 5** extended the conversation engine with a full, deterministic multi-turn appointment-booking
state machine (`BookingState`, persisted in `CallSession.engine_state`) — caller identification (reused
`find_matching_customer`), service-type classification, real availability lookup, deterministic slot
selection and yes/no confirmation (never the LLM's own output), governed appointment creation reusing
the existing `internal_test_adapter.py`'s idempotency-key dedup and double-booking check unchanged, a
deterministic emergency-keyword escalation path that never even calls the AI provider, and an
application-enforced tool allowlist (`_VOICE_ALLOWED_TOOLS`) checked before any tool call — not merely a
prompt instruction. 16 new tests, including one that provokes and verifies graceful handling of a real
double-booking race. No real PostgreSQL row-locking was exercised for that race (SQLite only) — the
existing calendar adapter's own comments already document this exact, pre-existing limitation honestly;
this phase did not change or need to change that code.

**Phase 6** replaced Phase 4's "buffer the whole call then batch-transcribe at the end" audio handling
with a genuine real-time pipeline: a real G.711 μ-law ⇄ PCM16 codec (`app/services/audio_codec.py`),
streaming STT/TTS provider abstractions (`StreamingSTTProvider`/`StreamingTTSProvider` — real Deepgram/
ElevenLabs WebSocket client code, honestly `NOT_CONFIGURED` without keys), and a real-time turn manager
(`app/services/voice_realtime_service.py`) doing real-audio-energy silence detection, a deterministic
silence/prompt/hangup policy, and barge-in (agent TTS playback is genuinely interrupted mid-stream when
the caller starts talking — proven in a test that streams partial audio then simulates caller speech).
Every transcribed utterance still flows through the exact same, unmodified Phase 5
`VoiceConversationService.handle_turn` — Phase 6 changed none of the booking/governance logic, only the
audio plumbing that feeds it. 28 new tests. No live Twilio/Deepgram/ElevenLabs connection has ever been
exercised — the streaming provider classes construct real, correct connection/message code per each
provider's public documentation but have never actually opened a live session.

## Not implemented at all (genuinely missing, not stubbed further this phase)

- **Gmail** — OAuth, send/draft/reply. `GmailAdapter.get_status()` is hardcoded
  `NOT_CONNECTED` with no env var backing it at all (unchanged from before Phase 12C).
- **Google Ads / Meta Ads** — account connection, campaign/spend/conversion retrieval. Two
  separate, inconsistent stub implementations exist (`app/integrations/adapters.py` and
  `app/marketing_ads/base.py`) — reconciling them was out of scope this phase; neither has a
  real client.
- **Google Business / local listings** — no adapter class exists at all; env vars are defined
  in `.env.example` but unused by any code.
- **Apollo / Clay / Instantly (outbound enrichment)** — `app/marketing_outbound/base.py`'s
  `NotConnectedApolloAdapter`/`NotConnectedClayAdapter`/`NotConnectedInstantlyAdapter` are
  hardcoded `NOT_CONNECTED` with no env vars defined anywhere; no real client.
- **ServiceTitan / Jobber** — no real OAuth/API code; same shallow-stub state QuickBooks was in
  before Phase 13.

## Tenant-scoped connection model (Phase 12D; real OAuth providers wired in Phase 13/14)

Phase 12C deferred this; Phase 12D built it; Phase 13 gave it its first real OAuth provider
(QuickBooks), Phase 14 its second (Google Calendar) — Stripe also uses this model, but with a
plain API key, not OAuth; see above. `integration_connections` (migration `0017`,
`app/models/integration.py::IntegrationConnection`) is a real, tenant-scoped table for providers
where each tenant has their OWN external account (QuickBooks, Google Calendar, Gmail, Google
Ads, Meta Ads — as opposed to Twilio/SendGrid/OpenAI/Anthropic above, which remain single
platform-level credentials, unchanged).

**Architecture**: `app/integrations/credential_store.py` — Fernet (AES-128-CBC + HMAC-SHA256,
authenticated) symmetric encryption for credential material at rest, keyed by
`INTEGRATION_CREDENTIAL_ENCRYPTION_KEY` (falls back to an insecure, publicly-known default in
dev; the app refuses to boot with `ENV=production` if this is unset — same pattern as
`JWT_SECRET`). `app/services/integration_connection_service.py::IntegrationConnectionService` —
the one place lifecycle logic lives (`NOT_CONNECTED → CONNECTING → CONNECTED → ERROR →
DISCONNECTED`); a provider registers a real `verify()` callback (decrypted credential in, a
real API call, `(ok, detail)` out) rather than duplicating connect/verify/disconnect plumbing
per router. **Phase 13**: a real verifier is now registered for `quickbooks` (a real
`GET .../companyinfo/{realmId}` call). **Phase 14**: a real verifier is now also registered for
`google_calendar` (a real `GET /calendars/primary` call — see the dedicated section above).
Gmail/Google Ads/Meta Ads still have no real OAuth client built — attempting to connect any of
them today produces an honest `ERROR` status with detail `"No real verifier implemented for
provider 'X'"` — never a fabricated `CONNECTED`.

**API**: `GET /api/v1/integrations/connections` (list own tenant's connections),
`POST .../connections/{provider}/connect` (store + immediately attempt real verification),
`POST .../connections/{provider}/verify` (re-verify), `POST .../connections/{provider}/disconnect`
— all gated by the existing `MANAGE_INTEGRATIONS` permission (was declared in `rbac.py` since
early phases but never actually wired to an endpoint before Phase 12D). Response bodies never
include the encrypted credential or any decrypted value.

**Tenant isolation**: enforced at three layers, each with dedicated tests — query layer
(every `IntegrationConnectionService` method takes `tenant_id` and filters by it; there is no
method that can return or mutate another tenant's row — 12 tests in
`tests/test_integration_connection_service.py`), API layer (`current_user.tenant_id` only, never
a client-supplied tenant id; a cross-tenant verify/disconnect attempt returns 404, not another
tenant's data — 6 tests in `tests/test_integration_connections_api.py`), and the DB layer
(`(tenant_id, provider)` unique constraint, `tenant_id` indexed column).

**A real bug found and fixed while building this**: `verify()` initially had no bounded timeout
around the registered verifier call — a real external API call that hangs (network partition,
stalled provider) would have hung the request indefinitely, the exact same class of bug found
and fixed in Phase 12B's `/ready` endpoint. Fixed with an explicit `asyncio.wait_for` (10s);
proven by a dedicated test that registers a verifier which `sleep`s forever and asserts the
call still returns `ERROR` within the test's own 5s outer bound, not by inspecting timing logs.

**Current verification status**: fully real and tested at the architecture level (encryption
round-trip, lifecycle transitions, tenant isolation, timeout handling — 31 tests total, all
passing on both SQLite and real PostgreSQL). **Phase 13**: QuickBooks now has a genuinely real
OAuth2 client, verifier, and callback flow wired through this exact model — self-contained
tested throughout, but never exercised against Intuit's real OAuth server (no platform app
credentials configured in this environment). **Phase 14**: Google Calendar has the identical
real OAuth2 client/verifier/callback flow (see the dedicated section above) — also never
exercised against Google's real OAuth server, same credential-absence reason. Gmail/Google Ads/
Meta Ads remain unconnected — each would need its own real OAuth app registration, redirect URI,
and token exchange, none of which exist yet.

## Webhook idempotency ledger

`webhook_events` (migration `0015`) is the shared idempotency + audit ledger for every inbound
webhook, real across providers: `(provider, external_event_id)` is a DB-enforced unique
constraint — proven directly by `test_duplicate_webhook_delivery_never_double_applies_a_payment`
(Stripe) and `test_duplicate_status_callback_is_ignored` (Twilio), both passing. A rejected
(invalid-signature) request is never persisted here — its `external_event_id` is unverified at
that point and trusting it as a dedup key would let a forged payload collide with a future real
one.

## Phase 25 — Live Provider Verification Runbook (not yet executed — credentials absent)

This is the exact safe sequence to run once real Stripe test-mode and/or QuickBooks sandbox
credentials become available. Nothing below has been executed — `STRIPE_SECRET_KEY`/
`STRIPE_WEBHOOK_SECRET`/`STRIPE_PUBLISHABLE_KEY`/`QUICKBOOKS_CLIENT_ID`/`QUICKBOOKS_CLIENT_SECRET`/
`QUICKBOOKS_REDIRECT_URI` are all confirmed absent as of this writing. **Only ever use TEST-MODE
Stripe keys (`sk_test_...`/`whsec_...` from a test webhook endpoint) and a QuickBooks SANDBOX
company — never production/live-mode credentials.**

### Stripe runbook

1. **Create test-mode Checkout** — set `STRIPE_SECRET_KEY=sk_test_...`, create a real quote with a
   deposit via `quotes.create_quote_draft` (staff) or the public flow, accept it (→
   `DEPOSIT_PENDING`), then call `POST /api/v1/public/quotes/{quote_id}/deposit/checkout?token=...`.
   *Local state*: no new DB row yet (Klaros doesn't persist the Checkout Session id itself, only
   relies on the webhook). *Provider state*: a real, open Stripe Checkout Session. *ID*: Stripe's
   own `cs_test_...`, returned in the response, not stored locally. *Idempotency key*:
   `klaros-quote-deposit-{quote_id}-{deposit_amount}`. *Cleanup*: none needed — an unpaid test-mode
   Checkout Session expires on its own (default 24h).
2. **Confirm amount/currency** — read the Checkout Session back (Stripe Dashboard or
   `GET /v1/checkout/sessions/{id}`), confirm `amount_total` equals `deposit_amount * 100` and
   `currency` matches the quote's currency.
3. **Verify deterministic idempotency key** — call step 1 a second time for the SAME quote before
   paying; confirm Stripe returns the cached, identical Checkout Session object (`id` unchanged).
   This is the one step that would actually observe Stripe's PROVIDER-side idempotency guarantee,
   not just Klaros' own LOCAL one.
4. **Complete the test payment** — on the hosted Checkout page, use Stripe's documented test card
   (`4242 4242 4242 4242`, any future expiry/CVC).
5. **Receive the real webhook** — this sandbox has no public HTTPS endpoint. Use the Stripe CLI
   (`stripe listen --forward-to <local-url>/api/v1/webhooks/stripe`) to forward real test-mode
   events to wherever the app is actually running; the CLI prints the matching
   `STRIPE_WEBHOOK_SECRET` to use. Never fabricate a webhook payload — only a genuine
   Stripe-forwarded event counts as this step.
6. **Verify signature handling** — confirm the request is accepted (200) with the CLI-provided
   secret, and confirm it's correctly REJECTED (400) if the secret is deliberately set wrong.
7. **Verify `WebhookEvent` persistence** — query `webhook_events` for the new row: `status ==
   PROCESSED`, correct `external_event_id`/`event_type`, `tenant_id` populated.
8. **Verify `Payment` persistence** — query `payments`: `provider == "stripe"`, `external_id` is
   the real PaymentIntent id, `amount` matches, `quote_id` set.
9. **Verify quote transition** — `quotes.status == "CONVERTED"`, `job_id` set.
10. **Verify Job creation** — exactly one `jobs` row for that `quote_id`.
11. **Verify Invoice creation** — this is a SEPARATE, staff-triggered step
    (`InvoiceService.create_draft_from_job`) — call it explicitly to continue the chain toward
    QuickBooks; confirm one `invoices` row linked to the job.
12. **Issue a test refund** — `finance.create_refund_request` (partial amount) then
    `finance.approve_refund`. *Idempotency key*: `klaros-refund-{refund_id}`.
13. **Verify `Refund` persistence** — `refunds` row, `status == COMPLETED`.
14. **Verify `Payment` status** — `PARTIALLY_REFUNDED` or `REFUNDED` depending on the amount.
15. **Verify `PAYMENT_REFUNDED` processing** — confirm the event was published and (if QuickBooks
    is also connected) the refund-sync subscriber fired.

### QuickBooks runbook

1. **OAuth authorization** — `GET /api/v1/integrations/quickbooks/authorize` as an authenticated
   staff user; confirm it redirects to Intuit's real sandbox consent page with the correct
   `client_id`/`redirect_uri`/`scope=com.intuit.quickbooks.accounting`.
2. **Callback/state verification** — after granting consent, confirm the callback rejects a
   tampered or expired `state` value (400) and accepts the genuine one.
3. **Token exchange** — confirm `quickbooks_callback` exchanges the real `code` for a real
   `access_token`/`refresh_token` via Intuit's OAuth token endpoint.
4. **Encrypted credential persistence** — query `integration_connections` directly at the DB
   level; confirm `encrypted_credential` is genuine Fernet ciphertext (not readable plaintext).
5. **Token refresh** — after the access token's normal ~1 hour expiry, trigger any sync call and
   confirm `refresh_access_token` fires and the connection's stored tokens are updated.
6. **Customer creation/reuse** — sync a real Klaros Customer; confirm a QuickBooks Customer is
   created (or an existing one matched) and `external_id` is persisted on the Klaros row.
7. **Invoice sync** — sync the Invoice from the Stripe runbook's step 11; confirm a QuickBooks
   Invoice exists with matching `DocNumber`/line amounts, `external_id`/`external_provider`
   persisted.
8. **Deposit payment sync** — `finance.sync_deposit_payment_to_quickbooks`; confirm a QuickBooks
   Payment exists, linked via `LinkedTxn` to the synced Invoice, `Payment.quickbooks_payment_id`
   persisted. *Idempotency key*: `klaros-deposit-payment-{payment_id}`.
9. **Ordinary invoice payment sync** — same for a non-deposit payment.
   *Idempotency key*: `klaros-invoice-payment-{payment_id}`.
10. **Split-payment sync** — record one Payment allocated across 2+ Invoices; confirm the resulting
    QuickBooks Payment has multiple `Line[]`/`LinkedTxn` entries, one per invoice, correctly summed
    if the same invoice appears twice.
11. **Refund receipt sync** — sync the refund from the Stripe runbook's step 13; confirm a
    QuickBooks RefundReceipt exists, linked to the original Payment,
    `Refund.quickbooks_refund_receipt_id` persisted.
12. **Deterministic requestid** — retry any of steps 8–11 (e.g. by re-invoking the same tool call);
    confirm the SAME `requestid` query parameter is sent both times — this is the step that would
    actually observe whether Intuit's API deduplicates on it (the PROVIDER-side guarantee, distinct
    from Klaros' own LOCAL guarantee of sending the same key).
13. **Retry/recovery behavior** — this is already proven safe via failure injection in
    `tests/test_phase23_quickbooks_persistence_failure.py`; live execution would only reconfirm it
    against the real API, not prove anything new.

**Cleanup**: Stripe test-mode objects and QuickBooks sandbox company data don't need cleanup for
cost reasons; a QuickBooks sandbox company can be fully reset from the Intuit Developer Dashboard
if its data needs to start fresh for a later run.

## Phase 27 — Production Operations Readiness (`GET /ready` migration-head check)

Not a Stripe/QuickBooks-specific finding, but relevant to this document because it affects
`GET /ready`'s trustworthiness as the signal an orchestrator uses before routing real traffic
(including to the Stripe webhook endpoint and QuickBooks sync paths documented above). Found and
fixed a real gap: `/ready` previously only ran `SELECT 1` against the database — proving
connectivity, not that the expected schema actually exists. Reproduced directly against a real,
connectable-but-completely-empty Postgres schema: `/ready` still reported `200`/`"ready"`. Fixed by
adding a migration-head check (`app/main.py`) comparing the database's `alembic_version` against
the code's expected Alembic head; a mismatch now correctly reports `not_ready`/`503`. See
`PROJECT_STATUS.md`'s and `PRODUCTION_AUDIT.md`'s Phase 27 sections for full detail, including the
necessary companion fix to the test harness (`tests/conftest.py`) that made this possible without
breaking every existing test.

## Phase 29 — `PAYMENT_RECEIVED`/`QUOTE_DEPOSIT_PAID` publish-after-commit reliability

Directly relevant here: this is the exact event chain that triggers QuickBooks payment sync
(`finance_handlers.py`'s `PAYMENT_RECEIVED` subscriber) and the quote→job conversion. Found and
fixed a real gap: `PaymentService.record_payment` and `QuoteService.mark_deposit_paid` both commit
their business state, then publish their event as a separate operation — if that publish call fails
(a real transient-outage scenario), the event was previously permanently lost, even after a fully
successful retry, because the retry path's own idempotency short-circuit never re-attempted the
publish. Practically: a Stripe deposit could be recorded and the Job correctly created, while the
QuickBooks payment-sync trigger (which depends on `PAYMENT_RECEIVED`) silently never fires for that
payment. Fixed by publishing unconditionally on both paths, relying on `EventBus.publish`'s
existing idempotency-key dedup for safety. See `PROJECT_STATUS.md`'s and `PRODUCTION_AUDIT.md`'s
Phase 29 sections for full detail, including the exact reproduction.
