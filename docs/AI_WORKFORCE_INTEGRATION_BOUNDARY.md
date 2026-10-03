# AI workforce (Halla AI) — the Klaros-side integration boundary

**Halla AI is a separate platform with its own repository.** It provides the AI workforce: voice,
inbound and outbound calls, qualification, appointment booking, support, outbound communication.
Klaros does **not** contain, copy or re-implement any of it, and the Halla repository was not
touched by this work.

## What exists in Klaros

| Piece | Location | What it is |
|---|---|---|
| Adapter contract | `backend/app/integrations/workforce/contract.py` | `WorkforceIntegration` ABC: `get_status`, `configure`, `deploy_agent`, `get_agent`, `update_agent`, `health_check`; plain dataclasses; the expected capability vocabulary |
| Honest placeholder | `backend/app/integrations/workforce/registry.py` | `PendingWorkforceIntegration`: always reports `NOT_CONNECTED`, `adapter_implemented=false`; every mutating call raises `WorkforceNotConnectedError` |
| Status API | `GET /api/v1/business-builder/workforce` | returns exactly what the adapter reports |
| UI | `/workforce`, the "AI workforce" node on the Business Map, the Business Home card | shows the status; no connect/deploy controls exist while no adapter is implemented |

The contract deliberately specifies **no transport**: no URLs, no auth scheme, no payload format.
Those belong to Halla's own API contract, which is not available inside Klaros.

## Status vocabulary

In the UI, an AI workforce whose adapter does not exist is shown as **Integration required** (nothing to connect),
not merely *Not connected*; *Not connected* is reserved for the case where an adapter exists but the tenant has
not connected it.

`NOT_CONNECTED` · `CONFIGURATION_REQUIRED` · `CONNECTED` · `ERROR`.
`CONNECTED` may only be returned after a real health check against the live external system has
just succeeded. Today nothing can return it.

## Expected capabilities (the contract's vocabulary, not a claim of what is deployed)

Voice · Incoming calls · Outgoing calls · Lead qualification · Appointment booking ·
Customer support · Outbound communication.

A business requirement is *workforce-addressable* when the shared capability vocabulary marks it so
(communication, lead qualification, scheduling). Such requirements make the **AI workforce** node
appear on the Business Map — as **Not connected**, and as planned because no adapter exists.

## To connect Halla later

1. Obtain Halla's documented API (auth, agent deployment, health endpoint).
2. Implement `HallaWorkforceIntegration(WorkforceIntegration)` in `app/integrations/workforce/`,
   storing per-tenant credentials through the existing `IntegrationConnection` +
   `credential_store.py` pattern (never a shared global credential).
3. Register it in `registry.py`. Nothing else changes — every consumer depends on the contract.
4. Add contract tests: `get_status` must not return `CONNECTED` without a successful live check.

Pending (requires information that is not available inside Klaros): Halla's external API contract.

## Boundary v2 — what Klaros now does on its side

Still true: **Halla is a separate platform; Klaros integrates with it and does not rebuild it.** The real
Halla API contract is not available inside Klaros, so everything below is an **adapter boundary, not a live
connection**. Nothing here contacts a network.

| Status | What |
|---|---|
| **LIVE** | nothing — no Halla connection exists |
| **READY FOR CONNECTION** | the adapter contract (`contract.py`), typed inbound events (`events.py`), the business-context pack (`context.py`), tenant-scoped event ingestion (`BusinessWorkforceService.ingest`), the lead interaction state model, the UI |
| **MOCK / DEV ONLY** | `dev_adapter.py` and `POST /business-builder/workforce/dev/events`, enabled only with `WORKFORCE_ADAPTER=dev`; events are stored with `simulated: true` and labelled "simulated" in the UI. The simulator always reports `NOT_CONNECTED`, `mode="development"`, `adapter_implemented=false` |
| **NOT IMPLEMENTED** | an adapter against Halla's real API, channels, agent deployment, test and activation |

### Events (Halla → Klaros)

`halla.interaction.started`, `halla.interaction.completed`, `halla.lead.qualified`, `halla.lead.escalated`,
`halla.appointment.requested`, `halla.appointment.confirmed`. They are published on the existing event bus
(durable `events` table, RLS), `entity_type="lead"`, with idempotency key `halla:{interaction_id}:{type}`. The
tenant is never part of the event — it is the authenticated caller's. `lead.qualified` sets the lead's
qualification (and `NEW`/`CONTACTED` → `QUALIFIED`); `lead.escalated` sets `REQUIRES_HUMAN`.

### Lead AI-interaction state (derived, never invented)

`NOT_CONNECTED` · `CONFIGURATION_REQUIRED` · `WAITING_FOR_HALLA` · `IN_PROGRESS` · `QUALIFICATION_PENDING` ·
`QUALIFIED` · `ESCALATED` · `APPOINTMENT_REQUESTED` · `APPOINTMENT_CONFIRMED`. With no recorded event the state is
the truth about the workforce ("Halla not connected"). Recorded events always win; the latest decides.

### Context pack (Klaros → Halla), `GET /business-builder/workforce/setup`

Business, customers, services, markets, qualification fields, escalation triggers and booking rules, built from the
Blueprint plus whatever an enabled industry module registers (`register_workforce_context_provider`). It is a
preview of what an adapter would send. These are business rules, not clinical logic.

### To connect Halla for real

Implement `HallaWorkforceIntegration` against Halla's documented API, register it in `registry.py`, have it call
`BusinessWorkforceService.ingest` for each inbound event (verified, per-tenant), and add contract tests. Nothing
else in Klaros changes.

## Real Halla integration (Klaros side) — `WORKFORCE_ADAPTER=halla`

Klaros talks to Halla over HTTPS only, from the backend only, with the **tenant's own** credential; Halla talks back with
signed webhooks. There is no shared database. Halla's repository is not touched and nothing of it is copied.

**Verified against Halla?** Not yet. The Halla-side contract is not present in the Halla repository or any local checkout
available to this work, and no Halla staging credentials exist here, so every Halla response in the tests is a labelled
mock and no live call has been made. The following are taken from the contract as given and are **assumptions to confirm
against Halla staging**: the credential header (no default — set `HALLA_API_KEY_HEADER`), the body of
`PUT /integrations/klaros/workforce` (camelCase business configuration, see `build_halla_workforce_config`), the shape of
`agents`/`health` responses (parsed tolerantly), the digest encoding (hex, optionally `sha256=`-prefixed) and timestamp
format (epoch seconds/ms or ISO-8601) of the webhook headers, and the field names inside each event's `data`.

| Concern | How |
|---|---|
| Routes | `GET /api/v1/integrations/klaros/{health,workforce,agents}`, `PUT …/workforce`, `POST /api/v1/leads`, `PUT /api/v1/leads/{id}`, `POST /api/v1/calls/outbound` — `halla_client.py` |
| Credential | one `IntegrationConnection` per tenant, `provider="halla"`; the encrypted blob (existing credential store) holds `{api_key, signing_secret, halla_tenant_id}`; `external_account_id` is the Klaros↔Halla tenant map. Never returned, never logged, never in the browser |
| Base URL | deployment config only (`HALLA_API_BASE_URL`, https in production, no userinfo/query, `HALLA_ALLOWED_HOSTS`); redirects are never followed; timeouts; only GET/PUT retried, bounded, only for retryable failures |
| Status | `CONNECTED` only from a real health request (`HALLA_STATUS_TTL_SECONDS` re-proves a stale one). Also `NOT_CONNECTED` (not configured), `CONNECTING`, `NEEDS_ATTENTION` (e.g. Halla reports a different tenant), `ERROR`, `CONFIGURATION_REQUIRED` (deployment not configured) |
| Leads | Klaros stays the source of truth. `klarosLeadId` is always sent; Halla's lead id is stored in `leads.external_provider/external_id` (migration 0064) and never replaces Klaros' id |
| Outbound call | `POST /calls/outbound` with `toNumber`, `reason`, `openingContext`, `klarosLeadId`; Halla executes the call |
| Webhook | `POST /api/v1/webhooks/halla/{klaros_tenant_id}`; `X-HallaAI-Timestamp`, `X-HallaAI-Signature = HMAC-SHA256(timestamp + "." + raw_body)` over the exact bytes, constant-time, freshness window; tenant resolved from the stored connection and `envelope.tenant_id` must equal the mapped Halla tenant; idempotent on the Halla event id (existing `webhook_events`); unknown types — including `appointment.requested`, which Halla never emits — are rejected |
| Events applied | `call.started/completed`, `lead.created/updated/qualified/escalated`, `appointment.confirmed/rescheduled/cancelled` → existing lead state, the existing event bus (so the existing automation engine runs workflows), and mirrored appointments |
| Qualification | `qualified`/`not_qualified`/`needs_human_review`/`unknown` → the existing lead status + qualification model; `unknown` never changes anything, and a booked/converted/lost lead is never pulled backwards |
| Appointments | Halla executes the bookings it makes; Klaros mirrors each as an `Appointment` (`external_provider="halla"`), never pushes it back, and never publishes the core `appointment.created` for it (that would double-book Klaros' own calendar sync) |
| PII | no transcripts are stored; only summary, outcome, qualification and Halla's call id |

Configuration names (values are never committed): `WORKFORCE_ADAPTER`, `HALLA_API_BASE_URL`, `HALLA_API_KEY_HEADER`,
`HALLA_API_KEY_SCHEME`, `HALLA_ALLOWED_HOSTS`, `HALLA_REQUEST_TIMEOUT_SECONDS`, `HALLA_MAX_RETRIES`,
`HALLA_STATUS_TTL_SECONDS`, `HALLA_WEBHOOK_TOLERANCE_SECONDS`, `KLAROS_PUBLIC_API_URL` (shown to the tenant as the webhook
address to register in Halla).
