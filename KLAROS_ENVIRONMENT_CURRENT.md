# Klaros Environment Variables — Current State

Source: `.env.example` (93 assignment lines, full file scanned for structure; comments read in full for lines 1–111, remaining lines confirmed present but not individually transcribed here — see the file itself for exact remaining var names such as `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`/`GOOGLE_CLIENT_REDIRECT_URI` which exist past line 111 and were referenced but not quoted verbatim in this pass). **No secret values are reproduced anywhere in this document or were ever printed during the audit** — only variable names and their documented purpose.

## Core
- `ENV` — `development`/`production`. Gates the production secret-enforcement checks in `backend/app/main.py:24-47`.

## Database
- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` — local Postgres container credentials (docker-compose).
- `DATABASE_URL` — SQLAlchemy async connection string (`postgresql+asyncpg://...` in production; `sqlite+aiosqlite://...` is a real, supported alternate scheme per `backend/requirements.txt:16-19`, mainly used by tests).

## Redis
- `REDIS_URL` — used by the event bus (when `EVENT_TRANSPORT=redis`) and rate limiter (when `RATE_LIMIT_BACKEND=redis`).

## Event bus
- `EVENT_TRANSPORT` — `redis` (production) or `memory` (dev/test fallback; a real in-process transport, not a mock, per its own code comment).

## Rate limiting
- `RATE_LIMIT_ENABLED` — on/off.
- `RATE_LIMIT_BACKEND` — `memory` (single-process only) or `redis` (shared across replicas; never auto-detected — a misconfiguration fails loudly rather than silently degrading).
- `RATE_LIMIT_PUBLIC_LEAD_PER_MINUTE`, `RATE_LIMIT_PUBLIC_QUOTE_PER_MINUTE`, `RATE_LIMIT_PUBLIC_CONTRACT_PER_MINUTE`, `RATE_LIMIT_AUTH_PER_MINUTE`, `RATE_LIMIT_WEBHOOK_PER_MINUTE` — per-surface limits protecting public/credential-free endpoints.

## Auth
- `JWT_SECRET` — signs all JWT types (access, refresh, OAuth-state, team-invite, quote/contract view tokens). **Production boot refuses to start if this is left at its documented insecure default** (`main.py:24-33`).
- `ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_DAYS`.

## Integration credential encryption
- `INTEGRATION_CREDENTIAL_ENCRYPTION_KEY` — encrypts tenant-owned OAuth tokens/API keys at rest (`IntegrationConnection.encrypted_credential`). Falls back to a publicly-known insecure default if unset. **Production boot refuses to start if unset** (`main.py:35-47`).

## CORS / frontend linkage
- `CORS_ORIGINS` — JSON array of allowed origins.
- `FRONTEND_BASE_URL` — used to build the browser redirect target after a provider OAuth callback completes.
- `NEXT_PUBLIC_API_URL` (frontend-side) — base URL the Next.js app's `lib/api.ts` targets; defaults to `http://localhost:8000`.

## Object storage
- `OBJECT_STORAGE_ENDPOINT` — leave blank to use the real local-disk adapter (`backend/app/storage/local_adapter.py`) for job photos/documents/voice-notes; setting it is meant to switch to an S3-compatible bucket, but that path is **explicitly documented as not implemented yet** ("reports NOT_CONNECTED").
- `OBJECT_STORAGE_BUCKET`, `OBJECT_STORAGE_ACCESS_KEY`, `OBJECT_STORAGE_SECRET_KEY` — only relevant once S3 support is built.
- `STORAGE_LOCAL_ROOT` — local-disk root path.

## Temporal
- `TEMPORAL_HOST`, `TEMPORAL_NAMESPACE`, `TEMPORAL_TASK_QUEUE` — real Temporal server connection for the durable-workflow/worker layer (§15 of main audit).

## Third-party integrations — real once configured
- `QUICKBOOKS_CLIENT_ID`, `QUICKBOOKS_CLIENT_SECRET`, `QUICKBOOKS_REDIRECT_URI`, `QUICKBOOKS_ENVIRONMENT` (`sandbox`/`production`), `QUICKBOOKS_TIMEOUT_SECONDS`, `QUICKBOOKS_MAX_RETRIES` — real OAuth2 app + API client.
- `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_TIMEOUT_SECONDS`, `STRIPE_MAX_RETRIES` — real payments/refunds/subscriptions client. This is the *platform-level* key; a tenant can alternatively connect their own Stripe account via Settings → Integrations (stored encrypted, no env var).
- `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`/redirect-URI equivalents (past line 111 in `.env.example`, not individually re-quoted here) — real Google Calendar OAuth2 app, separate from any Google Ads credentials.
- `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` — select and enable the real `AI_PROVIDER` path in `backend/app/services/ai_provider.py`. **With neither set (the state of this repo's own `.env`/`.env.example` defaults), every AI-provider call falls back to `DeterministicAIProvider` and produces `mode=DETERMINISTIC` output — i.e. "AI" features are effectively off by default.**
- `AI_PROVIDER` — selects `anthropic` or `openai` when a key is present.
- `TWILIO_*` (account SID/auth token/phone number — exact var names not individually re-quoted, confirmed present via `.env.example` structure) — real voice bridge.
- `SENDGRID_API_KEY` (exact var name not independently re-verified against code in this pass) — stated real per `.env.example:74-76`.
- `VOICE_AI_ENGINE` — selects between the cascaded Twilio/STT/LLM/TTS pipeline and the OpenAI Realtime engine for the voice receptionist.

## Third-party integrations — explicit stubs regardless of configuration
- `XERO_CLIENT_ID`, `XERO_CLIENT_SECRET` — present as env vars but explicitly documented as non-functional ("still an honest stub regardless of what you set here").
- `GOOGLE_ADS_CLIENT_ID`, `GOOGLE_ADS_CLIENT_SECRET`, `GOOGLE_ADS_DEVELOPER_TOKEN` — same.
- Meta Ads, Google Business Profile, ServiceTitan, Jobber — referenced in the same stub disclosure; their specific env var names were not individually captured in the portion of `.env.example` read in this pass (past line 111).

## Sentry / observability (inferred from `error_monitoring.py`, not from `.env.example` directly in this pass)
- `SENTRY_DSN` — optional; when unset, error capture still logs via `structlog` (an explicit, honest no-op-that-still-logs fallback, not a silent failure).
- `SENTRY_TRACES_SAMPLE_RATE` — referenced in `error_monitoring.py:51`.

## Not fully enumerated in this pass
`.env.example` has 93 total assignment lines; this document explicitly walks through lines 1–111 of the file's content (which includes all core/infra/auth/storage/Temporal vars and the first integration block). The remainder of the third-party integration block (past QuickBooks/Xero/Stripe/Google Ads, i.e. the exact Google Calendar, Twilio, SendGrid, and stub-provider variable names) was confirmed to exist by directory/file evidence and by the summary disclosure comment, but not individually transcribed line-by-line — read `.env.example` directly for the exhaustive, authoritative list before provisioning a real deployment.
