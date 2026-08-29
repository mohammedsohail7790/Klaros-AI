# Klaros AI — External Integrations (Phase 12C)

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
succeeds. Providers without a real client yet (QuickBooks, ServiceTitan, Jobber, Google Ads,
Meta Ads, Gmail) can only ever report `NOT_CONNECTED`/`ERROR`, honestly, since there is no real
call to make.

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

## Not implemented at all (genuinely missing, not stubbed further this phase)

- **QuickBooks** — OAuth flow, invoice/customer/payment sync. `QuickBooksAdapter.get_status()`
  still only checks whether `QUICKBOOKS_CLIENT_ID` is set; no real OAuth code exists.
- **Google Calendar** — OAuth, calendar discovery, event CRUD, duplicate-event prevention. The
  existing `CalendarProvider`/`InternalTestCalendarAdapter` abstraction (used by
  `calendar.check_availability`/`create_appointment`/etc.) is real and tested against itself,
  but there is no real Google adapter.
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
- **ServiceTitan / Jobber** — same shallow-stub state as QuickBooks; no real OAuth/API code.

## Tenant-scoped connection model (Phase 12D) — built, real, no OAuth provider wired to it yet

Phase 12C deferred this; Phase 12D built it. `integration_connections` (migration `0017`,
`app/models/integration.py::IntegrationConnection`) is a real, tenant-scoped table for
providers where each tenant has their OWN external account (QuickBooks, Google Calendar,
Gmail, Google Ads, Meta Ads — as opposed to Stripe/Twilio/SendGrid/OpenAI/Anthropic above,
which remain single platform-level credentials, unchanged).

**Architecture**: `app/integrations/credential_store.py` — Fernet (AES-128-CBC + HMAC-SHA256,
authenticated) symmetric encryption for credential material at rest, keyed by
`INTEGRATION_CREDENTIAL_ENCRYPTION_KEY` (falls back to an insecure, publicly-known default in
dev; the app refuses to boot with `ENV=production` if this is unset — same pattern as
`JWT_SECRET`). `app/services/integration_connection_service.py::IntegrationConnectionService` —
the one place lifecycle logic lives (`NOT_CONNECTED → CONNECTING → CONNECTED → ERROR →
DISCONNECTED`); a provider registers a real `verify()` callback (decrypted credential in, a
real API call, `(ok, detail)` out) rather than duplicating connect/verify/disconnect plumbing
per router. **No verifier is registered for any provider yet** — QuickBooks/Google Calendar/
Gmail/Google Ads/Meta Ads have no real OAuth client built (Phase 12D Step 10, not started — no
credentials to build against). Attempting to connect any of them today produces an honest
`ERROR` status with detail `"No real verifier implemented for provider 'X'"` — never a
fabricated `CONNECTED`.

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
passing on both SQLite and real PostgreSQL). **No real OAuth provider has been connected
through this model** — that requires Phase 12D Step 10 (a real Google/Intuit OAuth app
registration, a real redirect URI, and a real token exchange), none of which exist yet.

## Webhook idempotency ledger

`webhook_events` (migration `0015`) is the shared idempotency + audit ledger for every inbound
webhook, real across providers: `(provider, external_event_id)` is a DB-enforced unique
constraint — proven directly by `test_duplicate_webhook_delivery_never_double_applies_a_payment`
(Stripe) and `test_duplicate_status_callback_is_ignored` (Twilio), both passing. A rejected
(invalid-signature) request is never persisted here — its `external_event_id` is unverified at
that point and trusting it as a dedup key would let a forged payload collide with a future real
one.
