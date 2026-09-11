# Klaros AI — Deployment Runbook & Smoke Test

Every command below exists in this repository today (`docker-compose.yml`, `backend/Dockerfile`,
`frontend/Dockerfile`, `backend/app/main.py`, `frontend/package.json`) — nothing here is invented.
See `PRODUCTION_READINESS.md`/`PRODUCTION_AUDIT.md` for the underlying security/correctness evidence
this runbook assumes.

**Status distinction, read before anything else:** every claim in this file and in
`ARCHITECTURE_TRACEABILITY.md` about the production entrypoint, boot guard, `/health`/`/ready`,
docs-gating, and security behavior is **LOCAL PRODUCTION-MODE VERIFIED** — run directly with
`ENV=production` against real PostgreSQL/Redis/Temporal on this development machine, never against a
real cloud host. **Klaros is NOT actually cloud-deployed anywhere.** No hosting account, container
registry, managed database, managed Redis, or DNS record exists for it today. See the "Production
Deployment + Pilot Smoke Test" and "Deployment Target + Production Infrastructure Plan" entries in
`ARCHITECTURE_TRACEABILITY.md` for the most recent local production-mode verification evidence and
the recommended target architecture, respectively.

## Deployment order

1. **Provision PostgreSQL and Redis.** `docker-compose.yml` provisions both for local/staging use
   (`postgres`, `redis` services, each with a real healthcheck). For a real production deployment,
   point `DATABASE_URL`/`REDIS_URL` (below) at managed instances instead — the compose file's
   `postgres`/`redis` services are not intended as the production database.
2. **Configure secrets.** Copy `.env.example` to `.env` and fill in every `REQUIRED` variable below.
   The app **refuses to boot** (`app/main.py::_assert_production_secrets_are_real`) if `ENV=production`
   and `JWT_SECRET` is still the default, or if `INTEGRATION_CREDENTIAL_ENCRYPTION_KEY` is unset —
   this is enforced in code, not just documented.
3. **Run migrations** (once, from a machine with `DATABASE_URL` pointed at the real production DB):
   ```bash
   cd backend
   alembic upgrade head
   ```
   Migrations are never run automatically on container start (`backend/Dockerfile`'s `CMD` is only
   `uvicorn`) — this is a deliberate, separate, explicit step.
4. **Start the API:**
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```
   (`backend/Dockerfile`'s default `CMD`; the `--reload` flag in `docker-compose.yml`'s `backend`
   service is dev-only — do not use it in production.) If deploying via Docker Compose, use
   `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build` instead of the
   base file alone — `docker-compose.prod.yml` (Phase 33A) overrides the dev-only `--reload` and
   `npm run dev` commands with the real production ones, and requires `POSTGRES_PASSWORD` to be set
   explicitly rather than falling back to the dev default. The production Docker image is built from
   `backend/requirements.txt` only (`pytest`/`pytest-asyncio`/`pip-audit` moved to
   `backend/requirements-dev.txt`, Phase 33A) and excludes `.venv/`, `.git/`, and the test suite via
   `backend/.dockerignore`/`frontend/.dockerignore` (both new, Phase 33A).
5. **Verify `/health`:** `curl http://<host>:8000/health` → `{"status":"ok","env":"production"}`.
   This is liveness only (no dependency check) — a passing `/health` with a failing `/ready` means
   the process is up but not yet safe to route traffic to.
6. **Verify `/ready`:** `curl -i http://<host>:8000/ready` → HTTP 200 with `"status":"ready"` and
   every entry in `checks` equal to `"ok"` (`database`, `migration`, and `redis` unless
   `EVENT_TRANSPORT=memory`). A 503 here means don't route traffic yet — read the specific failing
   `checks` entry.
7. **Start the Event Worker** (consumes the durable event store via Redis Streams — retention,
   marketing, finance, operations, CRM, Morning Brief scheduling all stop propagating without it):
   ```bash
   python -m app.events.worker
   ```
8. **Start the Temporal Worker** (long-running workflows — invoice-overdue, lead-qualification,
   job-lifecycle; requires a reachable Temporal server, e.g. `docker-compose.yml`'s `temporal`
   service, or a managed Temporal Cloud namespace):
   ```bash
   python -m app.workers.main
   ```
9. **Build and start the frontend:**
   ```bash
   cd frontend
   npm install
   npm run build
   npm run start
   ```
   `NEXT_PUBLIC_API_URL` must point at the real, publicly reachable backend URL — it is baked into
   the build (`NEXT_PUBLIC_*` vars are inlined at build time in Next.js), so rebuild if it changes.
10. **Configure Stripe webhooks** (if accepting payments): in the Stripe Dashboard, add an endpoint
    at `https://<host>/api/v1/webhooks/stripe`, select the events the app consumes (see
    `INTEGRATIONS.md`), and copy the signing secret into `STRIPE_WEBHOOK_SECRET`. Restart the API
    after setting it.
11. **Configure QuickBooks OAuth** (if enabling QuickBooks sync): register an Intuit developer app
    with redirect URI `https://<host>/api/v1/integrations/quickbooks/callback`, set
    `QUICKBOOKS_CLIENT_ID`/`QUICKBOOKS_CLIENT_SECRET`/`QUICKBOOKS_REDIRECT_URI` (must match exactly)
    and `QUICKBOOKS_ENVIRONMENT` (`sandbox` or `production`). A tenant then connects via
    Settings → Integrations in the app (`GET /api/v1/integrations/quickbooks/authorize`).
12. **Create the first tenant:** `POST /api/v1/auth/register` (via the `/register` page or directly)
    with `organization_name`/`full_name`/`email`/`password`. This creates the tenant, the OWNER user,
    and returns access/refresh tokens.
13. **Configure inbound Twilio lead capture** (if enabled — outbound SMS already works once
    `TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN`/`TWILIO_FROM_NUMBER` are set): the Twilio integration is
    platform-level (one shared account across every tenant), so inbound routing uses a tenant_id
    embedded in the webhook URL, which the tenant pastes into their own Twilio phone number's
    console configuration — Twilio's signature covers that exact URL, so a tampered tenant_id fails
    verification. For the tenant created in step 12, configure that number's webhooks to:
    - Messaging → "A message comes in": `https://<host>/api/v1/webhooks/twilio/inbound-sms/<tenant_id>`
    - Voice → "A call comes in": `https://<host>/api/v1/webhooks/twilio/inbound-voice/<tenant_id>`
    Each creates a real, tenant-scoped `Lead` (source `TEXT`/`PHONE`) via the same `LeadService`
    every other lead source uses — no fabricated name/urgency/service; an unknown caller gets a
    plain, self-describing placeholder name built from their own number.
14. **Run the smoke test below.**
15. **Verify backup/recovery expectations for the chosen PostgreSQL provider** — this repository does
    not manage backups itself; that is the responsibility of wherever `DATABASE_URL` points (a
    managed Postgres provider's own backup/PITR settings).

## Environment variable contract

REQUIRED (app refuses to boot or is insecure without a real value in production):
`DATABASE_URL`, `JWT_SECRET` (must not be the default `change-me-in-production`),
`INTEGRATION_CREDENTIAL_ENCRYPTION_KEY` (must be set — falls back to a publicly-known key otherwise),
`CORS_ORIGINS` (must list the real frontend origin, not the default `localhost:3000`),
`FRONTEND_BASE_URL`, `NEXT_PUBLIC_API_URL` (frontend build-time).

REQUIRED IF `EVENT_TRANSPORT=redis` (the production setting): `REDIS_URL`.

DEVELOPMENT ONLY: `EVENT_TRANSPORT=memory` (in-process fallback; do not use in production — the
Event Worker becomes a separate, out-of-process consumer only when `EVENT_TRANSPORT=redis`).

PROVIDER-SPECIFIC (each integration honestly reports "Not connected" until its own variables are
set — nothing is faked as working without real credentials; see `.env.example`'s inline comments
and `INTEGRATIONS.md` for exactly what each connects): `STRIPE_SECRET_KEY`/`STRIPE_WEBHOOK_SECRET`,
`QUICKBOOKS_CLIENT_ID`/`_SECRET`/`_REDIRECT_URI`, `GOOGLE_CLIENT_ID`/`_SECRET`/`_REDIRECT_URI`,
`TWILIO_*`, `SENDGRID_*`, `OPENAI_API_KEY`/`ANTHROPIC_API_KEY`.

OPTIONAL: `OBJECT_STORAGE_*` (blank = real local-disk storage adapter, not a mock),
`TEMPORAL_HOST`/`_NAMESPACE`/`_TASK_QUEUE` (only needed if Temporal workflows are in use),
per-provider `*_TIMEOUT_SECONDS`/`*_MAX_RETRIES`.

Never commit `.env`. Secrets are never sent to the frontend — only `NEXT_PUBLIC_*` variables are
exposed to the browser, and none of the secret variables above use that prefix.

## Production smoke test

Run after every deploy. Expected result is listed for each stage — stop and investigate on the
first mismatch rather than continuing.

| Stage | Action | Expected result |
|---|---|---|
| Health | `GET /health` | `200`, `{"status":"ok"}` |
| Readiness | `GET /ready` | `200`, every `checks` entry `"ok"` |
| Auth / Tenant | `POST /api/v1/auth/register` | `201`, real `tenant_id` + access/refresh tokens |
| Lead | Create a lead via `/leads` (UI or `POST /api/v1/leads`) | Lead appears in the list; a real qualification score is computed automatically |
| Qualification | Open the lead detail page | Score, reason, and recommended next action are all real (not placeholder) |
| Appointment | Book a slot via `/calendar` | Appointment appears with `TENTATIVE` status, tied to the customer |
| Quote | Create + send a quote via `/quotes` | Quote reaches `SENT`; a real signed customer-view link is returned |
| Contract | Accept the quote via the public link | `CONTRACT_CREATED` fires automatically; contract visible at `/contracts` |
| Deposit | Sign the contract, check `/contracts/{id}` | Deposit state (outstanding/paid/not required) shown correctly |
| Job | (if no deposit required) | Job auto-created, visible at `/jobs` |
| Operations | Assign/schedule/dispatch the job | Status transitions correctly; illegal transitions are rejected |
| QA | Attempt QA completion with no attachment, then with one | First attempt rejected; second passes |
| Signoff | Record signoff | Real signoff row created |
| Invoice | Close the job | Invoice auto-created in `DRAFT` — allow up to `EVENT_WORKER_POLL_SECONDS` (1s default) for the event-driven creation to land before checking; an empty result immediately after closing is expected eventual-consistency lag, not a failure |
| Payment | Approve → send → pay the invoice | Invoice reaches `PAID`; AR reflects it |
| Retention | Check `/retention/opportunities` | A real opportunity appears, sourced from `job.closed` |
| Inbound lead capture (SMS/voice) | Text/call the tenant's configured Twilio number | A real `Lead` (source `TEXT`/`PHONE`) appears within seconds |
| Inbound lead capture (web/chat) | `POST /api/v1/public/leads/{tenant_id}` with `{"name","source":"WEB","email"}`, no auth header | `201`, a real `Lead` (source `WEB`) appears; repeating with the same `idempotency_key` does not create a second one |
| Review consent → marketing | Record a 5-star review, then `retention.record_review_consent`, then `marketing.create_content_from_review` | Without consent: rejected. With consent: a real `MarketingContent` DRAFT is created, requiring approval |
| Morning Brief | `POST /api/v1/morning-brief/generate` | Real insights/recommendations referencing the entities above |
| Approval | Execute an `APPROVAL_REQUIRED` recommendation | A real `ApprovalRequest` is created; the requester cannot approve their own request |
| AI Activity | Check `/ai-activity` | The above tool calls appear with correct actor/tenant/result |
| Rate limiting | Submit >10 requests/minute to `POST /api/v1/public/leads/{tenant_id}` from one caller | First ~10 succeed (`201`); the rest return `429` with a `Retry-After` header; a different tenant is unaffected |
| Marketplace lead ingestion | Connect `angi` via `POST /api/v1/integrations/connections/angi/connect` with a `webhook_secret`, then `POST /api/v1/webhooks/marketplace/angi/{tenant_id}` with a matching `X-Klaros-Signature` | `200`, a real `Lead` (source `MARKETPLACE`, source_detail `angi`) appears; an unconfigured tenant/provider gets `503`/`404`; a wrong signature gets `400` |
| AI Voice Receptionist | Enable it at `/settings/voice`, then POST a signed Twilio-shaped request to `/api/v1/webhooks/twilio/inbound-voice/{tenant_id}` | `200` with `<Connect><Stream>` TwiML; a real `CallSession` appears in `/settings/voice`'s call list. Without `DEEPGRAM_API_KEY`/an `AI_PROVIDER` key, a real call via the WebSocket honestly ends in a human handoff, never a fabricated resolution |
| Voice appointment booking | Drive `VoiceConversationService.handle_turn` through a full booking conversation (see `test_voice_appointment_booking.py` for the exact turn sequence) | A real `Appointment` row is created only after explicit confirmation; the call's `booking` state in `/settings/voice`'s call detail progresses through `COLLECTING_*` → `OFFERING_SLOTS` → `CONFIRMING_APPOINTMENT` → `APPOINTMENT_CREATED`; repeating the confirmation never creates a second appointment |
| Real-time voice pipeline (Phase 6) | Connect a WebSocket client to `wss://.../api/v1/voice-stream`, send `start` with real `tenant_id`/`call_session_id` customParameters, then `media` frames | With `STT_PROVIDER`/`TTS_PROVIDER`/`AI_PROVIDER` all configured with real keys: real transcription → real booking-flow turn → real synthesized audio streamed back as `media` events. Without them: an honest `PROVIDER_FAILURE`/`AI_FAILURE` outcome, never a fabricated resolution. `/settings/voice`'s call detail shows real per-turn latency (`conversation_ms`/`tts_ms`/`total_ms`) once at least one real-time turn has run |
| Knowledge ingestion | Edit a file at `/settings/knowledge`, switch to "Test retrieval", search for a phrase actually in it | With `OPENAI_API_KEY` set: a real embedded, ranked match. Without one: an honest "no embedding provider configured" message — never a fabricated result |
| Knowledge retrieval / tenant isolation | As two different tenants, put distinctive content in tenant A's knowledge, then search for it as tenant B | Tenant B's results never contain tenant A's content (enforced in the SQL `WHERE` clause, not a post-hoc filter) |

## Phase 8 — real phone call certification: blocked by credential

Pre-flight confirmed Phase 7's PostgreSQL/Redis/Temporal still running and both Phase 7 defect fixes
(`app/calendar/internal_test_adapter.py`'s advisory lock, `app/tools/redact.py`'s truncation) still
intact (14/14 re-passed against real Postgres; full suite 882 passed/11 skipped on SQLite, unchanged).
`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, `DEEPGRAM_API_KEY`,
`ELEVENLABS_API_KEY`, `OPENAI_API_KEY`, and `ANTHROPIC_API_KEY` are all unset. A real phone call needs
all of these; none are available, so none of Phase 8's live-call objectives could be attempted. To
actually run Phase 8: set real values for all seven in `backend/.env`, point a real Twilio number's
voice webhook at this deployment's `POST /api/v1/webhooks/twilio/inbound-voice/{tenant_id}`, enable the
receptionist at `/settings/voice`, and place a real call.

## Phase 7 — real local infrastructure (installed on this machine, with explicit consent)

At the user's explicit approval, real PostgreSQL, Redis, and Temporal were installed locally (no
Docker/Homebrew/sudo available or used) to genuinely verify — not simulate — infrastructure behavior
this engagement had only ever run against SQLite/in-memory transports before:

- **PostgreSQL 16.6** — via [Postgres.app](https://postgresapp.com) (no sudo required), data directory
  at `~/.local/var/postgres`, listening on `localhost:5432`. Start: `~/.local/opt/Postgres.app/Contents/Versions/16/bin/pg_ctl -D ~/.local/var/postgres -l ~/.local/var/postgres/server.log -o "-p 5432 -k ~/.local/var/postgres" start`.
- **Redis 8.10.1** — built from source (`redis-server`/`redis-cli` only; the optional
  RediSearch/RedisJSON/RedisTimeSeries/RedisBloom modules failed to build due to missing
  cmake/newer-make and were skipped — Klaros doesn't use them), installed at `~/.local/bin`. Start:
  `redis-server --port 6379 --dir ~/.local/var/redis --logfile ~/.local/var/redis/redis.log`.
- **Temporal** — official `temporal` CLI dev server (Server 1.31.2), installed at
  `~/.temporalio/bin`. Start: `temporal server start-dev --port 7233 --ui-port 8080 --db-filename ~/.local/var/temporal.db`
  (UI at `http://localhost:8080`).

These are real, persistent local services (not containers) — they keep running until stopped
(`pg_ctl ... stop`, killing the `redis-server`/`temporal` processes) and use real disk under `~/.local`
and `~/.temporalio`. Several throwaway databases were created during verification
(`klaros_test`, `klaros_test2`, `klaros_temporal`, `klaros_app`) — safe to `dropdb` any of them.

This closes PostgreSQL/Redis/Temporal from "never verified in this environment" to genuinely
live-verified (see ARCHITECTURE_TRACEABILITY.md) — but does **not** unblock Twilio/Deepgram/
ElevenLabs/a real LLM provider, which need real paid third-party accounts/API keys no local
installation can substitute for.

## Known limitations at this certification

- Real PostgreSQL/Redis concurrency **was** exercised as of Phase 7 (see the section above and
  ARCHITECTURE_TRACEABILITY.md) — including finding and fixing a genuine appointment double-booking
  race that only real PostgreSQL concurrency could expose. Live Stripe/QuickBooks/Twilio/Deepgram/
  ElevenLabs/LLM-provider calls remain BLOCKED BY CREDENTIAL — no real account for any of them exists
  in this environment. Run this smoke test against your real target infrastructure/credentials before
  considering a production deployment fully verified.
- AI Voice Receptionist (`/settings/voice`, `POST /webhooks/twilio/inbound-voice/{tenant_id}` when
  enabled, `WS /voice-stream`) needs `DEEPGRAM_API_KEY` and `ELEVENLABS_API_KEY` for real speech
  transcription/synthesis, plus an `AI_PROVIDER` key for the conversation engine itself — without all
  three, an inbound call honestly ends in a human handoff. Real-time bidirectional streaming to
  Deepgram/ElevenLabs is not implemented (only their REST/batch APIs are) — see INTEGRATIONS.md. No
  real Twilio call has ever connected to this in any environment this runbook was written in; test with
  a real Twilio number pointed at your deployment's inbound-voice webhook URL before considering this
  live-verified. Run `alembic upgrade head` (migration `0029`) before using it on an existing deployment.
- Knowledge-layer semantic search/RAG (`/settings/knowledge`, `knowledge.search`/`knowledge.ask`) needs
  `OPENAI_API_KEY` set for real embeddings — without it, every search/ask call honestly reports
  "no embedding provider configured" rather than degrading to a fake result. As of Phase 12,
  `KnowledgeChunk.embedding` is a real, fixed-width PostgreSQL `vector(1536)` column with an HNSW
  cosine-distance ANN index, and `knowledge.search` runs a genuine database-side
  `ORDER BY embedding <=> :query_vector` query — **your production PostgreSQL must support the
  `pgvector` extension** (`docker-compose.yml`'s `postgres` image already includes it; on a managed
  provider — RDS, Cloud SQL, Supabase, Neon — you may need to allow-list/enable the `vector` extension
  through the provider's own console before `alembic upgrade head` can run `CREATE EXTENSION
  IF NOT EXISTS vector` successfully). Run `alembic upgrade head` (migrations `0028` and `0032`) before
  using it on an existing deployment — `0032` converts any existing chunk rows into the new column type
  (rows with a missing/malformed embedding are deleted, since chunks are always fully regenerated by
  the next re-index, never partial data) and is a safe no-op if the table is empty.
- Rate limiting (`public/leads`, `public/quotes`, `public/contracts`, `auth/*`, inbound Twilio webhooks)
  is implemented (`backend/app/core/rate_limit.py`) with a real in-memory backend, verified by tests.
  The Redis-backed distributed backend (`RATE_LIMIT_BACKEND=redis`, set by default in
  `docker-compose.yml`'s `backend` service) has NOT been live-verified against a reachable Redis in this
  sandbox — confirmed to fail loudly (a real connection error) rather than silently fall back, but
  exercise it against real infrastructure before relying on it across multiple replicas.
- `npm run lint` is currently non-functional (Next.js 16 removed the `next lint` command and no
  `eslint` dependency has been added) — `npm run build`'s own TypeScript pass is the actual
  compile-time safety net in CI/deploy until this is addressed.
