# Klaros Integrations — Current State

Source of truth for this file: `backend/app/integrations/*.py`, `.env.example:69-111+`, `backend/app/api/v1/{integrations,quickbooks_oauth,google_calendar_oauth,google_calendar,webhooks,marketplace_webhooks}.py` (route files confirmed present via router mount, not all read line-by-line), and the project's own in-code honesty disclosures (spot-verified, not taken on faith — see `KLAROS_AI_CODEBASE_AUDIT.md` methodology note).

## Real, verified by direct code read

### Stripe
- File: `backend/app/integrations/stripe_client.py`.
- Direct `httpx` calls to `https://api.stripe.com/v1` (`stripe_client.py:40`), Bearer secret-key auth.
- Methods confirmed present: `verify_connection`, `create_payment_intent`, `create_checkout_session`, `create_customer`, `create_subscription_checkout_session`, `create_billing_portal_session`, `retrieve_payment_intent`, `create_refund` (`stripe_client.py:185-349`).
- Real webhook signature verification: `verify_webhook_signature(payload, sig_header, webhook_secret, tolerance_seconds=300)` (`stripe_client.py:349`).
- Two credential paths: platform-level key via `STRIPE_SECRET_KEY` (shared across tenants who haven't connected their own), or a tenant's own connected Stripe account stored encrypted via `IntegrationConnection` (no env var) — per `.env.example:97-104`.
- Purpose in-product: invoice/quote-deposit checkout, subscription billing for Klaros's own SaaS plans, refunds.
- Frontend surfaces: `createInvoiceCheckout`, `syncInvoiceToQuickBooks` (separate), `createStaffQuoteDepositCheckout`, `createPublicQuoteDepositCheckout`, `recordTestPayment` (`frontend/lib/api.ts` — line references in main audit §14/34).
- Env vars: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_TIMEOUT_SECONDS`, `STRIPE_MAX_RETRIES`.
- Status: **REAL / IMPLEMENTED**.

### QuickBooks
- File: `backend/app/integrations/quickbooks_client.py` + schemas `quickbooks_schemas.py`; OAuth routes `backend/app/api/v1/quickbooks_oauth.py`; tools `backend/app/tools/builtin/quickbooks_tools.py`.
- Real OAuth2 app; `QUICKBOOKS_ENVIRONMENT=sandbox|production` selects the Intuit API base. OAuth-state CSRF binding via `create_oauth_state_token`/`decode_oauth_state_token` (`backend/app/core/security.py:57-90`).
- Frontend surface: `syncInvoiceToQuickBooks` (`frontend/lib/api.ts:1240-1245`) — real invoice sync call.
- Env vars: `QUICKBOOKS_CLIENT_ID`, `QUICKBOOKS_CLIENT_SECRET`, `QUICKBOOKS_REDIRECT_URI`, `QUICKBOOKS_ENVIRONMENT`, `QUICKBOOKS_TIMEOUT_SECONDS`, `QUICKBOOKS_MAX_RETRIES`.
- Status: **REAL / IMPLEMENTED** (client/OAuth code read; live API round-trip not independently executed in this audit).

### Google Calendar
- Files: `backend/app/integrations/google_calendar_client.py` + `google_calendar_schemas.py`; OAuth routes `google_calendar_oauth.py`; sync route/service `google_calendar.py` / `google_calendar_sync_service.py`; tools `google_calendar_tools.py`.
- Real, tenant-scoped OAuth2 app, separate from QuickBooks's.
- Purpose: two-way appointment sync (`Appointment.external_provider`/`external_id` fields exist on the model, per `frontend/lib/api.ts:507`).
- Env vars: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_CLIENT_REDIRECT_URI` (exact var names per `.env.example`, not fully re-quoted here).
- Status: **REAL / IMPLEMENTED** (client/OAuth code read; not independently executed).

### Twilio
- File: `backend/app/integrations/twilio_client.py`; consumed by `backend/app/api/v1/{voice,voice_stream}.py` and `voice_conversation_service.py`.
- Used for the Media Streams voice bridge (inbound call → WebSocket audio → STT/LLM/TTS pipeline). Signature verification referenced by `marketplace_adapters.py:19` as the precedent Klaros' own marketplace-webhook HMAC scheme follows.
- Status: **REAL for voice**; SMS/outbound-messaging breadth via Twilio not independently re-verified in this pass — **PARTIAL/UNKNOWN** for non-voice channels.

### SendGrid, OpenAI, Anthropic
- Stated real the moment configured, per `.env.example:74-76`: "As of Phase 12C, STRIPE_*/TWILIO_*/SENDGRID_*/OPENAI_API_KEY/ANTHROPIC_API_KEY connect to REAL, working API clients."
- OpenAI/Anthropic usage independently confirmed via `backend/app/services/ai_provider.py` (provider selection, real HTTP calls gated behind API-key presence, deterministic fallback otherwise) and `backend/app/services/openai_realtime_voice_service.py` (OpenAI Realtime API for voice).
- SendGrid usage (presumably transactional email — invite emails, quote/contract/invoice send notifications) was **not independently read** in this pass — flagged **UNKNOWN, trusted on the strength of the same disclosure line that held up for Stripe/OpenAI**, not independently verified.
- Status: OpenAI/Anthropic **REAL / IMPLEMENTED** (AI provider). SendGrid **PROBABLY REAL, NOT INDEPENDENTLY VERIFIED**.

## Explicit stubs (per `.env.example:82-85`, spot-checked against code presence)

- **Xero** — `XERO_CLIENT_ID`/`XERO_CLIENT_SECRET` env vars exist; no client file found in `backend/app/integrations/` (only `quickbooks_client.py` exists for accounting). Status: **SCAFFOLD/STUB**.
- **Google Ads** — env vars exist (`GOOGLE_ADS_CLIENT_ID`, `GOOGLE_ADS_CLIENT_SECRET`, `GOOGLE_ADS_DEVELOPER_TOKEN`); no dedicated client file found. Status: **STUB**.
- **Meta Ads** — referenced in `.env.example:82-85`'s stub list; no client file found. Status: **STUB**.
- **Google Business Profile** — referenced in the same stub list. Status: **STUB**.
- **ServiceTitan** — referenced in the same stub list. Status: **STUB**.
- **Jobber** — referenced in the same stub list. Status: **STUB**.

All six are explicitly called out in-code as "still an honest stub regardless of what you set" for their env vars (`.env.example:82-85`) — i.e. even supplying credentials does not make them work; this is a deliberate, disclosed limitation, not a bug.

## Config-driven, non-API adapters

### Marketplace lead ingestion (Angi, Thumbtack, Nextdoor)
- File: `backend/app/integrations/marketplace_adapters.py` (full file read).
- These three providers have no public self-serve webhook API (researched and documented in-code, `marketplace_adapters.py:1-12`). Klaros does **not** fabricate a fake integration; instead it exposes a generic, per-tenant HMAC-signed inbound webhook plus a configurable dotted-JSON-path field map (`default_field_map`, `normalize_lead`) that the tenant fills in based on whatever bridge they actually have (e.g. the provider's own manual webhook config, or Zapier).
- `connect()` for these providers honestly lands in `ERROR`/"no verifier registered" status (no live API exists to verify against) — but the stored secret is still used by the webhook handler regardless of that status flag, which reflects live-verification only, never "is a secret configured."
- Credentials reuse the same `IntegrationConnection` model as QuickBooks/Google Calendar/Stripe.
- Status: **REAL generic webhook normalizer, NOT a live Angi/Thumbtack/Nextdoor API integration** — this distinction matters for how it should be represented to end users.

## Credential storage & security

- Per-tenant OAuth tokens / API keys stored encrypted at rest: `IntegrationConnection.encrypted_credential`, encryption/decryption in `backend/app/integrations/credential_store.py`.
- Production boot refuses to start if `INTEGRATION_CREDENTIAL_ENCRYPTION_KEY` is unset (`backend/app/main.py:35-47`) — the unset value would otherwise fall back to a publicly-known default key, per the code's own comment.
- Webhook signature verification: real for Stripe (`stripe_client.py:349`); Twilio verification referenced as an existing pattern; marketplace webhooks use tenant-specific HMAC.

## Frontend surfacing

`frontend/app/settings/integrations/page.tsx` exists as the presumed UI for connecting these providers. Its content was **not read in this pass** — whether it visually distinguishes "real" from "stub" integrations to the end user is **UNKNOWN** and flagged as a P2 item in the main audit (§31).
