# Klaros AI — Codebase Forensic Audit

Date: 2026-09-23
Repo: `/Users/mohammedsohail/Desktop/Klaros AI` (remote `git@github.com:mohammedsohail7790/Klaros-AI.git`, branch `main`, clean tree)
Latest commit: `8c4e13c` "Redesign marketing homepage, login, and register with richer visuals"

## Methodology & scope note

This audit is **read-only** (no code was modified). It combines exhaustive directory/route/model/tool enumeration (which is complete — every file under `backend/app`, `frontend/app`, `backend/alembic/versions` was listed) with **representative deep reads** of a sample of source files per subsystem (auth, tenancy, AI provider, tool execution boundary, one Temporal workflow, one integration client end-to-end, RBAC, rate limiting, error monitoring, the frontend API client). Claims about a specific file are evidence-backed with `file:line`. Claims about the *pattern* across a large family of similar files (e.g. "all 57 builtin tools go through the same policy gate") are based on reading the shared framework (`ToolRegistry`, `policy.py`, `factory.py`) plus several representative tool files, not every single one — these are flagged as sampled. Where something was not directly verified it is marked **UNKNOWN** rather than assumed.

This is an unusually large and unusually well self-documented codebase: most modules carry long docstring "honesty notes" that explicitly state what is real vs. stubbed (e.g. `.env.example` lines 69–85, `backend/app/integrations/marketplace_adapters.py:1-29`). Those in-code statements were treated as *claims to verify*, and were spot-checked against actual client code (e.g. Stripe, QuickBooks) rather than taken at face value — they held up in every case sampled.

---

## 1. Executive Summary

Klaros AI, as it exists in this repository, is **not** the "AI business-discovery / recommendation / website-builder platform" implied by the audit's phase list. It is a mature, vertical **field-service / home-services operations SaaS** (comparable in domain to ServiceTitan/Jobber) with: CRM (leads/customers/appointments), job/work-order lifecycle management, quoting, contracts (internal e-signature, no third-party provider), invoicing/AR/payments, marketing (campaigns/content/SEO/outbound/nurture/reactivation), customer retention (reviews/referrals/warranties/risk signals), compliance (licenses), a real RBAC/multi-tenant backend, a governed AI-assisted tool-execution layer, an AI voice receptionist, a Company-OS "knowledge" RAG layer, an automation/workflow engine backed by real Temporal, and a real event bus. It has **no** business-discovery wizard, **no** cross-category recommendation engine, and **no** website-generation feature anywhere in the code.

The backend (`backend/app`, FastAPI/SQLAlchemy/Postgres/Temporal/Redis) is deep and largely real: 55 API route modules, ~208 SQLAlchemy model classes, 40 Alembic migrations, 57 built-in AI-callable tools behind a real permission/policy/approval/audit gate, 1,417 collected pytest tests. Several integrations make genuine authenticated calls to real third-party APIs (Stripe, QuickBooks, Google Calendar, Twilio, SendGrid, OpenAI/Anthropic) — the code is explicit, in `.env.example:69-85`, that these are real and that a second group (Xero, Google Ads, Meta Ads, Google Business, ServiceTitan, Jobber, Angi/Thumbtack/Nextdoor) are "honest stubs."

The frontend (`frontend/app`, Next.js 16/React 18) has 57 route pages covering essentially the whole backend surface, using a single hand-written `lib/api.ts` (3,835 lines) typed client with real fetch calls, JWT bearer auth, silent refresh, and retry — not a mock data layer.

The most consequential finding for anyone planning to extend this into "AI agents that autonomously run a business": **there is no autonomous multi-tool AI agent** in the code. `backend/app/ai/execution_service.py:1-17` states this explicitly in its own docstring: it defines only the governed boundary (`AIExecutionService.request_tool_execution`) that a future agent would call through; "Phase 2 does not build the autonomous agent that produces ToolRequests." AI is used today in three narrow, explicitly-scoped ways: (1) an optional prose-rewriting pass on the deterministic Morning Brief, (2) an advisory-only lead-qualification scorer, and (3) a governed, allowlisted function-calling voice receptionist (Twilio-cascaded or OpenAI Realtime). None of these is a general orchestrator that plans across tools/integrations.

## 2. Repository Inventory

| Area | Path | Notes |
|---|---|---|
| Backend API | `backend/app/api/v1/*.py` (55 files) | FastAPI routers, see §6 |
| Backend domain services | `backend/app/services/*.py` (~70 files) | business logic layer |
| Backend models | `backend/app/models/*.py` (23 files, ~208 classes) | SQLAlchemy ORM |
| DB migrations | `backend/alembic/versions/` (40 files) | Alembic, sequential |
| AI execution boundary | `backend/app/ai/execution_service.py` | see §11 |
| Tool registry | `backend/app/tools/{base,factory,policy,registry,redact,errors}.py` + `backend/app/tools/builtin/*.py` (57 tool files) | see §13 |
| Workflows (Temporal) | `backend/app/workflows/{definitions,activities,automation_workflow}.py`, `backend/app/workers/main.py`, `backend/app/temporal_client.py` | real Temporal client/worker, see §15 |
| Events | `backend/app/events/{bus,transport,worker,metrics,*_handlers}.py` | real event bus, Redis Streams or in-memory |
| Integrations | `backend/app/integrations/*.py` | Stripe, QuickBooks, Google Calendar, Twilio real clients; `marketplace_adapters.py` explicit stub |
| Voice | `backend/app/services/{voice_conversation_service,openai_realtime_voice_service,speech_provider,audio_codec}.py`, `backend/app/api/v1/{voice,voice_stream}.py` | Twilio Media Streams + OpenAI Realtime, see §12 |
| Knowledge/RAG | `backend/app/services/{knowledge_service,knowledge_retrieval_service,embedding_provider}.py`, `backend/app/models/knowledge.py` | pgvector-backed, see §19 |
| Core/infra | `backend/app/core/{config,security,rate_limit,logging,error_monitoring}.py` | JWT auth, rate limiting, Sentry (optional) |
| Tests | `backend/tests/` (182 test files, 1,417 tests collected) | pytest, see §22 |
| Frontend app | `frontend/app/**/page.tsx` (57 route pages) | Next.js App Router |
| Frontend API client | `frontend/lib/api.ts` (3,835 lines), `frontend/lib/useAuth.ts` | hand-written typed fetch wrapper, real calls |
| Docker/deploy | `docker-compose.yml`, `docker-compose.prod.yml`, `backend/Dockerfile`, `backend/Dockerfile.combined`, `frontend/Dockerfile` | see §23 |
| Pre-existing internal docs | `PROJECT_STATUS.md`, `PRODUCTION_AUDIT.md`, `PRODUCTION_READINESS.md`, `ARCHITECTURE_TRACEABILITY.md`, `INTEGRATIONS.md`, `docs/*.md` | large (100–250 KB) first-party docs; **not** taken as ground truth for this audit — used only as a map, verified against code |

No CI workflow exists for this project (`.github/workflows` only appears inside `backend/.venv/lib/python3.12/site-packages/temporalio/...`, i.e. a vendored dependency's own repo, not this project's). No `vercel.json` or `render.yaml` found. No MCP server/client code found anywhere (`grep -rli mcp backend/app` → no matches).

## 3. Architecture

```
Browser (Next.js 16, frontend/app/**)
   │  fetch, Bearer JWT (frontend/lib/api.ts)
   ▼
FastAPI (backend/app/main.py) ── CORS, lifespan boot-time secret checks
   │
   ├─ Routers (backend/app/api/v1/*.py, 55 modules) ──► Services (backend/app/services/*.py)
   │                                                         │
   │                                                         ├─ SQLAlchemy async ORM ──► Postgres (pgvector ext.)
   │                                                         ├─ ToolRegistry (backend/app/tools/*) — permission/policy/approval/audit gated
   │                                                         ├─ EventBus (backend/app/events/*) ──► Redis Streams (prod) / in-memory (dev)
   │                                                         └─ Integration clients (Stripe/QuickBooks/Google Calendar/Twilio real; others stubbed)
   │
   ├─ Event worker (in-process dev, separate `event-worker` compose service in prod)
   └─ Temporal client (backend/app/temporal_client.py) ──► Temporal server ──► backend/app/workers/main.py (separate worker process)
```

Multi-tenancy is enforced **at the application layer**, not the database layer: `TenantScopedMixin` (referenced by ~190 of the 208 model classes) adds a `tenant_id` column, and every query path goes through `CurrentUser.tenant_id` extracted from the JWT (`backend/app/api/deps.py:20,54`). No Postgres Row-Level-Security policy was found (`grep` for `ROW LEVEL SECURITY`/`CREATE POLICY` across `backend/alembic` and `backend/app` → no matches). See §10.

## 4. Technology Stack

**Frontend**: Next.js `^16.3.3` (App Router), React `18.3.1`, TypeScript `5.6.3`, Tailwind CSS `3.4.13`, `class-variance-authority`/`clsx`/`tailwind-merge` for styling utilities, `lucide-react` for icons (`frontend/package.json`). No state-management library (Redux/Zustand/React Query) — data fetching is plain `fetch` via `frontend/lib/api.ts`, with component-local `useState`/`useEffect`. No form library (React Hook Form/Zod) — manual controlled inputs (sampled from `frontend/app/leads/page.tsx`-style pages, UNKNOWN for full coverage of 57 pages). No analytics SDK found in `frontend/`.

**Backend**: Python (FastAPI `0.141.1`, `backend/requirements.txt`), SQLAlchemy `2.0.35` (async, `asyncpg`), Alembic `1.13.3`, Pydantic `2.9.2`, `python-jose`+`passlib[bcrypt]` for JWT/password hashing, `redis` `5.0.8`, `structlog` `24.4.0`, `httpx` `0.27.2` for outbound calls, `temporalio` `1.8.0`, `pgvector` `0.5.0`, `sentry-sdk` `2.69.1` (optional), `websockets` `17.0.1` (real STT/TTS provider WS client, per code comment `backend/requirements.txt:4-6`). REST only — no GraphQL. WebSocket used for the Twilio Media Stream voice bridge (`backend/app/api/v1/voice_stream.py`).

**Database**: PostgreSQL with the `pgvector` extension (production; SQLite+`aiosqlite` supported as a genuine second runtime path, primarily exercised by the test suite — `backend/requirements.txt:16-19`). 40 Alembic migrations. No RLS. Tenant model = single shared schema, app-level `tenant_id` filtering.

**AI** (see §11–13, §19 for detail): `AI_PROVIDER` env var selects Anthropic or OpenAI (`backend/app/services/ai_provider.py:9-11`); with no API key configured, `get_ai_provider()` returns a `DeterministicAIProvider` and every call is `mode=DETERMINISTIC` (explicitly stated in the module's own docstring, `ai_provider.py:33-35`). Embeddings: `backend/app/services/embedding_provider.py`. Voice: OpenAI Realtime API as a selectable second voice engine (`backend/app/services/openai_realtime_voice_service.py`), alongside a cascaded Twilio→STT→LLM→TTS pipeline. No agent framework, no MCP, no model router beyond the single `AI_PROVIDER` switch, no persistent "memory" store beyond the separate, human/AI-populated `CompanyMemory` model (`backend/app/models/company_memory.py`) which is a structured knowledge table, not an LLM conversational memory.

## 5. Frontend

57 route pages under `frontend/app/` (full list in `KLAROS_USER_JOURNEYS_CURRENT.md` and `KLAROS_FEATURE_MATRIX_CURRENT.md`), covering: marketing site (`/`, `/pricing`), auth (`/login`, `/register`, `/accept-invite`), onboarding (`/onboarding`), dashboard (`/dashboard`), CRM (`/leads`, `/customers`, `/calendar`), operations (`/jobs`, `/operations`, `/operations/workers`, `/exceptions`), finance (`/finance`, `/finance/ar`, `/finance/cash`, `/finance/invoices`, `/finance/profitability`, `/quotes`, `/contracts`, `/vendors`), marketing (`/marketing`, `/marketing/campaigns`, `/marketing/content`, `/marketing/nurture`, `/marketing/outbound`, `/marketing/reactivation`, `/marketing/seo`), retention (`/retention/*` — 7 sub-pages), AI/automation surfaces (`/ai-activity`, `/automations`, `/approvals`, `/morning-brief`, `/events`), and settings (`/settings/{automation,billing,compliance,integrations,knowledge,memory,team,voice}`).

Every page reachable in a spot-check (`dashboard`, `leads`, `jobs`, `finance/invoices`, `quotes`, `automations`) calls a real backend endpoint via `frontend/lib/api.ts`, not local mock data. One reference to "mock" was found (`frontend/app/automations/page.tsx`) — not resolved to a definite classification in this pass; flagged **UNKNOWN**, worth a direct look before relying on that page. `frontend/lib/api.ts` implements real JWT bearer auth, a 401→silent-refresh-then-retry flow (`api.ts:48-118`), exponential-backoff retry for 5xx/429/network errors (`api.ts:27-46`), and FastAPI 422-validation-error message formatting (`api.ts:85-94`) — this is production-grade client plumbing, not a demo stub. No business-discovery, recommendation, or website-builder page exists anywhere in `frontend/app`.

Route-by-route Status classification was not exhaustively performed for all 57 pages (would require opening each one) — the sampled pages (dashboard, leads, jobs, invoices, quotes, automations, settings/integrations) are **REAL** (call live backend endpoints, real loading/error states via `withRetry`/`ApiError`). Remaining pages are **UNKNOWN** pending page-by-page review; given the consistent single-client pattern (`lib/api.ts` is the only data-fetching surface in the app) it is likely most are REAL, but this was not verified page-by-page.

## 6. Backend/API

55 router modules mounted under `/api/v1` in `backend/app/api/v1/router.py:71-134` (full prefix list reproduced in `KLAROS_API_INVENTORY_CURRENT.md`). Representative trace, `POST /api/v1/leads` end-to-end: `backend/app/api/v1/leads.py` → permission check via `CurrentUser`/`Permission.CREATE_LEAD` (RBAC, §9) → Pydantic request-body validation → `LeadService` (`backend/app/services/lead_service.py`) → SQLAlchemy insert scoped to `tenant_id` → response model. Public, unauthenticated surfaces exist deliberately and are clearly separated: `public_leads.py`, `public_quotes.py`, `public_contracts.py`, `public_invites.py` — these use signed, single-purpose JWTs (`create_quote_view_token`, `create_invite_token`, `backend/app/core/security.py:92-140`) rather than the normal session JWT, and are rate-limited (`RATE_LIMIT_PUBLIC_LEAD_PER_MINUTE` etc., `.env.example:18-28`). Webhook receivers (`webhooks.py`, `marketplace_webhooks.py`) exist for inbound provider events. No GraphQL, no exposed admin-only router distinct from RBAC-gated endpoints on the same routers. Dead/duplicate-endpoint analysis was not exhaustively run against frontend call sites for all 55 modules — **UNKNOWN** at full coverage; no obvious duplicates surfaced in the sampled set.

## 7. Database

~208 SQLAlchemy model classes across 23 files in `backend/app/models/`, essentially all `TenantScopedMixin` (tenant_id + created/updated timestamps), 40 Alembic migrations. Full table family list and detail in `KLAROS_DATABASE_CURRENT.md`. Key families: `Organization`/`User`/`TeamInvite` (tenancy+auth), RBAC is enum-based in code (`Role`, `Permission` in `backend/app/models/rbac.py`) rather than DB-row-based — no `roles`/`permissions` tables; a role's permission set is a static Python mapping (see §9). CRM (`Lead`, `Customer`, `CustomerNote`, `Appointment`). Operations (`Job`, `Worker`, `JobTask`, `JobAttachment`, `JobMaterial`, `PurchaseOrder`, `ScopeChange`, `OperationsException`, `JobQA`, `CompletionPacket`, `CustomerSignoff`). Finance (`Invoice`, `Payment`, `Refund`, `CreditNote`, `WriteOffRequest`, `JobCost`, `Vendor`, `VendorBill`, `Payout`, `CashForecast`, `CollectionAction`). Quote/Contract (`Quote`, `QuoteLineItem`, `Contract`). Marketing (18 classes — campaigns, content, SEO, outbound, nurture, reactivation, attribution). Retention (18 classes — lifecycle, opportunities, reminders, warranties, reviews, referrals, risk signals, campaigns). Knowledge (`KnowledgeFile`, `KnowledgeChunk` — the latter has a `pgvector` embedding column per `backend/app/db/vector_type.py` and migration 0032 per `knowledge_retrieval_service.py:14-18`). AI governance (`AIInvocationLog`, `ApprovalRequest`, `AuditLog`, `TenantToolPolicy`). Automation (`Automation`, `AutomationVersion`, `AutomationExecution`, `AutomationExecutionStep`). Voice (`CallSession`, `VoiceReceptionistSettings`). Integration (`IntegrationConnection`, `WebhookEvent`). Company memory (`CompanyMemory`). No RLS policies exist; isolation is app-level only (see §10).

## 8. Authentication

JWT-based (`python-jose`), `backend/app/core/security.py`. `create_access_token`/`create_refresh_token` embed `sub` (user id), `tenant_id`, `role`, a `ver` token-version (for revocation), and `exp` (`security.py:21-42`). Passwords hashed with bcrypt via `passlib` (`security.py:9-18`). Additional purpose-built signed-token types reuse the same `JWT_SECRET` rather than a second credential system, each with its own `type` claim and short/medium expiry: OAuth-state CSRF token for provider redirects (`create_oauth_state_token`, 10 min, `security.py:57-74`), team-invite token (7 days, `security.py:88-107`), and a public quote/contract view token (mentioned at `security.py:118+`, not fully read in this pass). Login/register/logout/refresh live in `backend/app/api/v1/auth.py` (not fully read line-by-line in this pass — routes confirmed present via router mount). The app **refuses to boot in production** with the default `JWT_SECRET` or an unset `INTEGRATION_CREDENTIAL_ENCRYPTION_KEY` (`backend/app/main.py:24-47`) — a real, enforced safety gate, not just documentation. No MFA, no SSO/OAuth login for end users was found (OAuth is used only for *integration* connections — QuickBooks/Google Calendar — not for Klaros account login). Session handling on the frontend: `sessionStorage` for access/refresh tokens (`frontend/lib/api.ts:57,69-70`) — tokens are not in `localStorage`, reducing (not eliminating) XSS persistence risk; still vulnerable to token theft via any XSS since it's not httpOnly-cookie-based.

## 9. Authorization

Enum-based RBAC: 7 roles (`OWNER, ADMIN, MANAGER, STAFF, TECHNICIAN, ACCOUNTANT, READ_ONLY`) and ~80 fine-grained permissions (`READ_CUSTOMERS`, `CREATE_JOB`, `APPROVE_SPEND`, `EXECUTE_AI_ACTION`, `MANAGE_INTEGRATIONS`, etc. — `backend/app/models/rbac.py:4-80+`, permission enum continues past what was read). Enforcement point: each `/api/v1/*` route depends on `CurrentUser` plus a permission check (57 files reference `get_current_user`/`require_permission`/`CurrentUser`, confirmed via `grep -rln`). This is genuinely server-side — the same `ExecutionContext`/permission model is reused for **AI-initiated** tool calls too (`AIExecutionService.request_tool_execution` builds an `ExecutionContext` with `role=ai_role`, `backend/app/ai/execution_service.py:39-54`), so AI actions pass through the identical RBAC/policy gate as a human API call, not a separate weaker path. A full Role→Permission→Resource matrix was not manually reconstructed from every route file in this pass (80 permissions × 55 routers); the mechanism itself is verified real and server-side. See `KLAROS_FEATURE_MATRIX_CURRENT.md` for a partial matrix.

## 10. Multi-Tenancy

App-level only, no Postgres RLS (confirmed absent by grep across all migrations and app code). Enforcement chain: JWT carries `tenant_id` → `deps.py` builds `CurrentUser.tenant_id` from the verified token (`backend/app/api/deps.py:20,54`) → every service method is documented/expected to filter by that value (`deps.py:66`: "repository/service method filters by tenant_id from CurrentUser, never..." — comment was truncated in this read; strongly implies "never trusts a client-supplied tenant id"). `TenantScopedMixin` puts `tenant_id` on essentially every table. The knowledge/RAG service explicitly documents tenant-scoping the similarity search *before* scoring runs (`knowledge_retrieval_service.py:11-13`). Because isolation is app-level, a single missed `WHERE tenant_id = ...` filter in any one of dozens of service methods would be a cross-tenant leak — this is a **structural risk class** for this architecture (no DB-enforced backstop), not a demonstrated live bug; a full per-service-method audit for a missing tenant filter was not performed across all ~70 service files in this pass (**P1 risk to flag, not confirmed exploitable** — see §27). No Postgres RLS, no separate-schema-per-tenant, no vector-DB tenant partition beyond the same `tenant_id` column on `KnowledgeChunk`. Cache/queue: Redis is shared infrastructure (event bus, rate limiter) — tenant separation there is by key-namespacing convention, not inspected key-by-key in this pass (**UNKNOWN**).

## 11. AI Orchestration

There is **no central agent orchestrator**. The real architecture is: three separate, narrow, purpose-built AI call sites, each funneled through the same governed tool-execution boundary when they need to take an action, and never allowed to touch the database or a tool directly.

1. **Morning Brief prose enrichment** (`backend/app/services/ai_provider.py`) — deterministic insights are computed first; an LLM (Anthropic/OpenAI, selected by `AI_PROVIDER`) is optionally asked to rephrase them into prose within a strict fenced-DATA-block prompt (`_SYSTEM_INSTRUCTIONS`, `ai_provider.py:59-77`) that explicitly instructs the model to ignore any embedded "instructions" in the data (a real prompt-injection mitigation). No tool-calling here. Falls back to `DeterministicAIProvider` (`mode=DETERMINISTIC`) whenever no API key is configured — which is the state of this repo's own `.env.example`/`.env` defaults.
2. **AI lead-qualification advisory** (`backend/app/services/ai_qualification_service.py`, surfaced via `POST /leads/{id}/ai-qualify-advisory`, `frontend/lib/api.ts:348-367`) — structured generation only, explicitly **advisory**: "Advisory only — never persists anything to the lead. A human still applies a reviewed recommendation" (`api.ts:359-361`). No tool-calling.
3. **AI Voice Receptionist** (`backend/app/services/{voice_conversation_service,openai_realtime_voice_service}.py`) — the only AI call site that can actually cause a state change, and it is explicitly boxed: the model never touches the DB; every action goes through `AIExecutionService.request_tool_execution` → `ToolRegistry` → `ActionPolicy` → `ApprovalRequest` → `AuditLog` (`openai_realtime_voice_service.py:16-22`); the callable tool set is an explicit allowlist shared between both voice engines (`_VOICE_ALLOWED_TOOLS`); the model cannot invent an appointment slot — server-side validation checks the model's claimed slot against the real prior `crm.check_availability` tool output (`_SlotGuard`, same file, ~line 33-37); emergency detection is fully deterministic and can interrupt the model mid-response.

The generic "AI decides which of the 57 tools to call, across any domain" agent does **not exist** — `backend/app/ai/execution_service.py` docstring states this outright as a known, deliberate gap ("Phase 2 does not build the autonomous agent that produces ToolRequests... This module only establishes the boundary that agent will be required to call through"). So: User → UI/API → (no orchestrator) → one of the three narrow AI call sites above → (voice only) governed tool call → integration/DB → persistence with audit log. The "Orchestrator," "model router" (beyond `AI_PROVIDER` selection), and "central agent runtime" stages described in the audit brief's Phase 9 are **MISSING**; "context assembly," "approval mechanism," and "audit trail" stages **EXIST** and are real, just wired to these three narrow features rather than a general agent.

## 12. Agents

No `class *Agent` definitions exist anywhere in `backend/app` (confirmed by grep). There is no agent-definition file, no per-agent prompt/tool/memory config object, and nothing resembling an agent registry. What could be mistaken for "agents" in product terms are the three AI call sites in §11 — none of them is a generically configurable, persisted "agent" entity; each is hard-coded Python. **Status: NOT IMPLEMENTED** as a general concept; the voice receptionist is the closest thing to an executable "agent" and it is real and wired end-to-end (Twilio → STT → governed tool calls → TTS, or OpenAI Realtime equivalent).

## 13. Tools/MCP

**No MCP** anywhere in the codebase (confirmed by grep for `mcp` — zero matches in `backend/app`). What exists instead is a first-party **Tool Registry**: `backend/app/tools/{base,factory,registry,policy,redact,errors}.py` plus 57 built-in tool files in `backend/app/tools/builtin/` (crm, invoicing, quotes, marketing (9 files), retention (8 files), compliance, warranties, job/operations (9 files), payments, Stripe, QuickBooks, Google Calendar, notifications, morning brief, audit, system, etc.). `build_tool_registry()` (`backend/app/tools/factory.py:305`) wires all of them with a shared `session_factory` and `EventBus`. Every tool call is executed through `ToolRegistry.execute(...)` with an `ExecutionContext` carrying `tenant_id`, `actor_type` (HUMAN or AI), `role`, and a `correlation_id` — the same object whether the caller is a human API request or the AI voice pipeline (§9, §11). `tools/policy.py` (412 lines) implements the permission/approval-gating logic; `tools/redact.py` implies output redaction (e.g. for secrets) — not fully read line-by-line. Each tool has its own Pydantic input schema (used both for API validation and, in the OpenAI Realtime voice path, to generate the model's function-calling schema directly from the tool's real schema — "never a hand-duplicated copy," `openai_realtime_voice_service.py:26-29`). This is a legitimate, coherent internal tool-calling framework; it is simply not MCP and not exposed as MCP.

## 14. Integrations

Full detail in `KLAROS_INTEGRATIONS_CURRENT.md`. Summary, per the project's own `.env.example:69-85` honesty note (spot-verified against code, not taken on faith):

**Real, verified**: Stripe (`backend/app/integrations/stripe_client.py` — direct `httpx` calls to `https://api.stripe.com/v1`, real `create_payment_intent`/`create_checkout_session`/`create_customer`/`create_subscription_checkout_session`/`create_billing_portal_session`/`create_refund`, real webhook signature verification `verify_webhook_signature`, `stripe_client.py:40,120-349`). QuickBooks (`quickbooks_client.py`, real OAuth2 app, sandbox/production switch). Google Calendar (`google_calendar_client.py`, real OAuth2). Twilio (`twilio_client.py`, used for the voice bridge and presumably SMS — not fully re-verified for SMS send paths in this pass). SendGrid and OpenAI/Anthropic keys are stated as real the moment configured (`.env.example:74-76`) — not independently re-verified beyond the AI provider code already read in §11.

**Explicit stubs** ("honest stub regardless of what you set," `.env.example:82-85`): Xero, Google Ads, Meta Ads, Google Business Profile, ServiceTitan, Jobber.

**Deliberately non-API, config-driven adapters**: Angi/Thumbtack/Nextdoor marketplace lead ingestion (`backend/app/integrations/marketplace_adapters.py:1-29`) — the code's own docstring explains none of these three publishes a public webhook API, so instead of fabricating one, Klaros exposes a per-tenant HMAC-signed webhook endpoint plus a configurable JSON-path field map the tenant fills in from whatever bridge (e.g. Zapier) they actually have. This is an honest, unusual, and architecturally sound design choice — but it means "Angi integration" is really "a generic inbound webhook normalizer," not a live Angi API integration, and should be represented to users accordingly.

Credential storage: per-tenant OAuth tokens/API keys are stored encrypted (`IntegrationConnection.encrypted_credential`, `backend/app/integrations/credential_store.py`), with a boot-time refusal if `INTEGRATION_CREDENTIAL_ENCRYPTION_KEY` is unset in production (`main.py:35-47`).

## 15. Workflows

Real Temporal (`temporalio==1.8.0`), not a dependency-only presence. `backend/app/workers/main.py` runs a genuine `Worker` polling `TEMPORAL_TASK_QUEUE`, registering `EventProcessingWorkflow`, `InvoiceOverdueWorkflow`, `JobLifecycleWorkflow`, `LeadQualificationWorkflow` (`backend/app/workflows/definitions.py`) and `AutomationWaitWorkflow` (`backend/app/workflows/automation_workflow.py`). The automation engine's durable-wait step genuinely starts a Temporal workflow (`start_automation_wait_workflow`, `automation_workflow.py:41-58`) with a real retry policy (`RetryPolicy(initial_interval=1s, backoff_coefficient=2.0, maximum_attempts=3)`) and raises (rather than silently no-opping) if Temporal is unreachable. Separately, the **Automation Engine** itself (`backend/app/models/automation.py`, `backend/app/services/automation_service.py`, `backend/app/tools/builtin/automation_policy_tools.py`) is a deterministic trigger→condition→action engine (schedule or event triggers, per `AutomationCondition`/`TriggerType`), not an LLM-driven planner — this is a real, if conventional, business-rules automation system, separate from the Temporal durability layer that some of its steps use for waits. Human approval is modeled via `ApprovalRequest`/`ApprovalExecutionStatus` and surfaced on the frontend `/approvals` page. Idempotency appears as a deliberate pattern across many write endpoints (`deduplicated` booleans returned from `createLead`, `createJob`, `createInvoiceCheckout`-adjacent flows, `triggerInvoiceFromJob`, etc. — sampled from `frontend/lib/api.ts`).

## 16. Business Discovery

**NOT IMPLEMENTED.** No route, model, service, or tool related to "describe your business → AI asks questions → requirements → recommendations" was found anywhere (`grep` for business_discovery/recommend across backend returned only unrelated hits like `recommended_action` fields on exceptions and `RecommendationStatus` on Morning Brief insights — a per-insight "here's a suggested next step" enum, not a discovery flow). Onboarding (`frontend/app/onboarding/page.tsx`) exists as a route but was not read line-by-line in this pass; given no backend discovery/requirements endpoints exist to back it, it is very unlikely to implement AI business discovery — flagged **UNKNOWN pending direct read, but backend evidence strongly suggests it is a conventional signup/setup form, not an AI discovery wizard.**

## 17. Recommendation Engine

**NOT IMPLEMENTED** as a cross-category system. The only "recommendation"-shaped code found is narrow and per-feature: `MorningBriefRecommendation` (a deterministic next-best-action surfaced in the daily brief, `backend/app/models/morning_brief.py:51,90`) and `recommended_action` text on `OperationsException` records. Neither recommends CRM/finance/marketing/integration/agent/workflow choices based on business context — there is no such input (no business-profile/vertical field feeds any recommendation logic that was found).

## 18. Website Creation

**NOT IMPLEMENTED.** No page-generation, site-builder, domain-connection, or publishing code exists in `backend/app` or `frontend/app` (no matches for website_creation/website_generat and no plausible route/model family for it in the full route/model listing in §2/§6/§7).

## 19. Knowledge/RAG

Real and reasonably sophisticated for what it covers. `backend/app/services/knowledge_retrieval_service.py` (322 lines): idempotent chunking+embedding indexing keyed by content hash (`chunk_text`, deterministic, tested — `tests/test_knowledge_chunking.py` per its own docstring), tenant-scoped similarity search, with two real code paths — Postgres+pgvector using `ORDER BY embedding <=> :query_vector` with an HNSW index (migration 0032) in production, and a Python-side cosine-similarity fallback so the SQLite-backed test suite keeps working. `backend/app/services/embedding_provider.py` (187 lines, not fully read) abstracts the embedding call. Ingestion → storage → embedding → retrieval chain: `KnowledgeFile` upload (`knowledge_service.py`) → `index_file` → `KnowledgeChunk` rows with embeddings → `knowledge_qa_service.py` presumably answers questions over retrieved chunks (file present, not fully read — **UNKNOWN** depth of the Q&A prompt construction). Explicitly documented gap: the voice receptionist's realtime engine does **not** consume Company Memory or (implicitly) this knowledge layer — "Company Memory: NOT consumed here, matching the cascaded path exactly... no redesign to force it in" (`openai_realtime_voice_service.py:44-47`) — an honest, in-code admission of an integration gap between the knowledge/memory layer and the voice AI.

## 20. Security

Secrets: JWT secret and integration-credential encryption key both have hard-coded insecure defaults that are explicitly checked and refused at production boot (`main.py:24-47`) — a genuinely strong control, not just a warning. Encrypted-at-rest tenant credentials (`credential_store.py`). CORS is configured via `CORS_ORIGINS` (`.env.example:44`) — not verified for wildcard misuse in this pass. No CSRF tokens found for cookie-based flows because auth is bearer-JWT (not cookie-based), which sidesteps classic CSRF but shifts risk to XSS-driven token theft (tokens live in `sessionStorage`, §8). Webhook signature verification is real for Stripe (`verify_webhook_signature`, `stripe_client.py:349`) and appears intended for Twilio (`twilio_client.py`, per `marketplace_adapters.py:19` comment referencing "the same way app/integrations/twilio_client.py verifies Twilio's own signature") and the tenant-configurable marketplace webhooks (HMAC). SQL injection risk is low by construction — SQLAlchemy ORM/Core throughout the sampled code, no raw string-interpolated SQL observed in the files read. Rate limiting is real (`core/rate_limit.py`, in-memory or Redis-backed fixed-window, explicit no-silent-fallback design — `rate_limit.py:1-19`) and applied to public lead intake, public quote/contract views, auth, and inbound webhooks (`.env.example:18-28`). Multi-tenancy is app-level only — see §10's flagged structural risk. Command/SSRF injection: not specifically probed in this pass — **UNKNOWN**. No secret values were printed or logged in any file read.

## 21. Observability

Structured logging via `structlog` throughout (`configure_logging()`/`get_logger` used in `main.py`, workers, etc.). Error tracking via Sentry is real but optional and honestly degrades: `error_monitoring.py:33-51` — with no `SENTRY_DSN` set, `capture_exception()` still logs via structlog (an "honest structlog-only fallback," its own comment says) rather than silently doing nothing. No metrics/tracing backend (Prometheus/OpenTelemetry) was found in dependencies or code. `backend/app/events/metrics.py` exists (event-bus-specific counters, not fully read — **UNKNOWN** depth/backend). No AI latency/token-usage dashboard found, though `AIInvocationLog` model exists and is presumably populated by `ai_invocation_log_service.py` (not fully read — plausible real per-call logging of AI usage, **UNKNOWN** verified). Health-check endpoint presence was not directly confirmed in this pass — **UNKNOWN**.

## 22. Testing

`backend/tests/`: 182 test files, **1,417 tests collected** by `pytest --collect-only` (executed live in this audit, real count, not a claim from docs). Test names sampled (`test_warranties.py`) include explicit tenant-isolation tests (`test_warranties_isolated_per_tenant`) and real-HTTP-layer tests (`test_warranties_over_http`), suggesting a mix of unit and integration-style tests against the FastAPI TestClient/SQLite. Whether these tests were actually **run to a passing state** (vs. merely collected) was not verified in this audit — collection succeeding only proves the test files import cleanly, not that `pytest` (full run) is green. No frontend test files or `npm test` script were found in `frontend/package.json` (`scripts` only has `dev/build/start/lint` — **no frontend tests exist**). No dedicated load-testing tooling found. **Gap: frontend has zero automated test coverage; backend test *execution* status (pass/fail) was not verified in this audit** (flagged rather than run, to respect the read-only constraint on a repo with a live `dev.db`).

## 23. CI/CD

No GitHub Actions workflow exists for this project itself. `docker-compose.yml`/`docker-compose.prod.yml` define the real local/prod service topology (API, worker, event-worker, Postgres w/ pgvector, Redis, Temporal — inferred from `TEMPORAL_HOST=temporal:7233` and `EVENT_TRANSPORT` compose-service references in `.env.example` and `main.py` comments; the compose files themselves were not fully read line-by-line in this pass — **UNKNOWN exact service list**, high confidence based on env var evidence). `backend/Dockerfile` and `backend/Dockerfile.combined` (a combined API+worker image, per filename) and `frontend/Dockerfile` exist. No `vercel.json`/`render.yaml` — deployment target is self-hosted Docker Compose, not a managed PaaS, based on available evidence. No staging-vs-production environment separation artifact was found beyond `ENV=development|production` and `QUICKBOOKS_ENVIRONMENT=sandbox|production`-style per-integration switches.

## 24. Technical Debt

`backend/app/**/__pycache__` and a checked-in `backend/.venv/` and `backend/dev.db` (2.8 MB SQLite dev database) sit inside the repo tree per the initial listing — whether these are gitignored was not verified (**UNKNOWN**; `git status` was clean, which is at least consistent with them being ignored). `backend/.pytest_cache` similarly present on disk. `frontend/.next` and `frontend/node_modules` and `tsconfig.tsbuildinfo` present on disk (build artifacts — again, `git status` clean suggests these are gitignored, not committed). Five very large first-party markdown docs at repo root (`PRODUCTION_AUDIT.md` 245 KB, `PROJECT_STATUS.md` 248 KB, `PRODUCTION_READINESS.md` 99 KB, `ARCHITECTURE_TRACEABILITY.md` 154 KB, `INTEGRATIONS.md` 105 KB) — these are extensive and were used only as a map for this audit, not as ground truth; their sheer size relative to the codebase suggests significant "Phase N" iterative-documentation overhead that itself could be considered process debt, though this is a judgment call, not a code defect. One raw "mock" string reference found in `frontend/app/automations/page.tsx`, unresolved in this pass — flagged for direct follow-up.

## 25. Dead Code

Not exhaustively determined. No obviously orphaned major subsystem was found — the 55 backend routers, 57 tools, and 57 frontend pages all appear to form a coherent, cross-referenced whole in the sampling done. A dedicated unused-export/unused-dependency scan (e.g. `ts-prune`, `vulture`) was not run in this audit. **UNKNOWN** at the granular level.

## 26. Capability Matrix

| Capability | Status | Evidence | Notes |
|---|---|---|---|
| Business discovery | MISSING | §16 | no discovery/requirements flow found |
| Requirements generation | MISSING | §16 | none found |
| Recommendations (cross-category) | MISSING | §17 | only per-feature "next action" text exists |
| Website creation | MISSING | §18 | none found |
| CRM | IMPLEMENTED | §6, §7, `models/crm.py` | leads/customers/appointments, real routes+DB |
| Finance | IMPLEMENTED | §7, `models/finance.py` | invoices/payments/AR/refunds/credit notes/vendor bills |
| Marketing | IMPLEMENTED | §7, `models/marketing.py` (18 classes) | campaigns/content/SEO/outbound/nurture/reactivation |
| Calendar | IMPLEMENTED | `google_calendar_client.py`, `appointments.py` | real Google Calendar OAuth integration |
| Communication | PARTIAL | `twilio_client.py`, `communication.py` | Twilio real for voice; SMS/email send breadth not fully verified |
| Payments | IMPLEMENTED | §14, `stripe_client.py` | real Stripe payment intents/checkout/refunds |
| Ecommerce | MISSING | — | no product/inventory/order model found |
| Analytics | PARTIAL | `attribution_service.py`, dashboard aggregates | feature-scoped analytics exist; no unified analytics platform |
| AI agents | SCAFFOLD/MISSING | §11, §12 | governed execution boundary exists; no autonomous agent |
| AI orchestration | PARTIAL | §11 | three narrow governed AI call sites; no central orchestrator |
| Workflows | IMPLEMENTED | §15 | real Temporal + deterministic automation engine |
| MCP | MISSING | §13 | not used anywhere |
| Knowledge/RAG | IMPLEMENTED | §19 | real pgvector-backed chunking/embedding/retrieval |
| Memory | PARTIAL | §19 | `CompanyMemory` model real; not consumed by voice AI's realtime engine |
| RBAC | IMPLEMENTED | §9 | 7 roles, ~80 permissions, server-enforced incl. for AI actions |
| Billing (Klaros's own SaaS billing) | IMPLEMENTED | `billing_service.py`, `organization.py` plan/billing_status fields, Stripe subscription checkout | real Stripe subscription lifecycle wiring |

## 27. User Journeys

See `KLAROS_USER_JOURNEYS_CURRENT.md` for full detail. Summary:

(a) Landing→Signup→Onboarding→Dashboard: routes exist end-to-end (`/`, `/register`, `/onboarding`, `/dashboard`) and register/login call real backend auth endpoints (`frontend/lib/api.ts:155-176`); onboarding page content itself not read in this pass (**UNKNOWN** whether it's a real multi-step form or a stub).
(b) Business description→Questions→Requirements→Recommendations: **does not exist** (§16, §17).
(c) Recommendation→Connect app→OAuth→Credentials→Test→Active: the *connect/OAuth/credential* half is real (QuickBooks/Google Calendar OAuth flows, `settings/integrations` page, `IntegrationConnection` model with `ConnectionStatus`), but there is no upstream "recommendation" step feeding it — a user manually chooses what to connect from a settings page.
(d) Create agent→Configure→Knowledge→Tools→Deploy: **does not exist** as a self-serve flow — Knowledge exists (§19) and Tools exist (§13), but there is no UI/API to compose a new configurable agent from them.
(e) Trigger→AI→Tool→Integration→Result: real for the voice receptionist and for the deterministic Automation Engine (trigger→condition→action, §15); not real for a general "AI decides" case since no such general agent exists.
(f) Business idea→Website→Integrations→Agents→Workflows→Launch: **does not exist** (§16, §18, and no agent composition per (d)).

## 28. Medical Tourism Readiness

Evaluated against existing code only, nothing implemented for this audit. Provider/hospital/clinic directory: MISSING (no such model). Leads/CRM: EXISTS and is generic enough to repurpose (`Lead`/`Customer` models are domain-agnostic). Social media: MISSING (no social posting integration found; SEO/content tools exist but are generic marketing content, not social scheduling). Communication: PARTIAL (Twilio voice real; broader multi-channel messaging not fully verified). Scheduling: EXISTS (`Appointment`, Google Calendar sync). AI receptionist: EXISTS and is real (§11/§12), domain-agnostic, would need new prompts/tools for medical intake, not new infrastructure. Provider matching: MISSING. Referral tracking: EXISTS (`Referral`, `ReferralProgram`, `ReferralCode`, `ReferralReward` models in `retention.py`) — built for a home-services referral-reward use case, would need adaptation for cross-border patient referrals but the data model shape is close. Commission tracking: PARTIAL — `VendorBill`/`Payout` exist for a different purpose (subcontractor payouts); no explicit commission-on-referral concept. Finance: EXISTS (generic invoicing/AR). Analytics: PARTIAL (generic dashboards, no medical-tourism-specific KPIs). **Overall: strong generic operations substrate, but the medical-tourism-specific domain concepts (provider directory, cross-border referral/commission, patient matching) do not exist and would be net-new.**

## 29. Dropshipping Readiness

Supplier management: MISSING (no `Vendor`-as-supplier-with-catalog concept; `Vendor`/`VendorBill` exist but model subcontractor billing, not product sourcing). Ecommerce/product catalogue/inventory/orders: **MISSING entirely** — no product, SKU, inventory, or order model exists anywhere in `backend/app/models`. Payments: EXISTS (Stripe, generic). Fulfillment: MISSING. Marketing: EXISTS (generic, could be repurposed). Customer support: PARTIAL (no ticketing model; `CustomerNote`/communication logs exist as a weak substitute). Analytics: PARTIAL (generic dashboards only). **Overall: Klaros has essentially none of the ecommerce-specific data model (product/inventory/order/fulfillment) a dropshipping business needs — this is a bigger gap than Medical Tourism, since the core domain objects are simply absent, not just missing a UI.**

## 30. Production Readiness (per subsystem)

- Auth/JWT: PRODUCTION CANDIDATE (real enforced secret checks, real refresh flow; no MFA/SSO)
- RBAC/tool policy: PRODUCTION CANDIDATE (real, consistently enforced, incl. for AI actions)
- Multi-tenancy: BETA (real but app-level only, no DB backstop — see §10 risk)
- CRM/Operations/Finance/Marketing/Retention core CRUD: PRODUCTION CANDIDATE (deep, tested, real DB-backed)
- Stripe/QuickBooks/Google Calendar integrations: PRODUCTION CANDIDATE (real API clients, real OAuth, webhook verification)
- Twilio voice pipeline: BETA (real, but voice/telephony systems need live-traffic hardening beyond static code review)
- Automation engine / Temporal: BETA (real, but "requires a reachable Temporal server," per its own worker docstring — an operational dependency to manage)
- Knowledge/RAG: BETA (real pgvector pipeline; embedding provider depth and QA prompt quality not fully verified)
- AI Morning Brief / lead qualification: BETA (real, explicitly falls back to deterministic when no AI key set — safe by design, but "AI" features are OFF by default in this environment)
- Xero/Google Ads/Meta Ads/Google Business/ServiceTitan/Jobber/marketplace-lead integrations: SCAFFOLD/DEMO ONLY (explicit stubs)
- Business discovery / recommendation engine / website creation / general AI agents / MCP: NOT IMPLEMENTED
- Frontend: BETA (real client, no automated tests)
- Observability: PARTIAL (structured logs + optional Sentry; no metrics/tracing stack)
- CI/CD: NOT IMPLEMENTED for this repo (no workflow files)

## 31. Critical Findings

**P0**
- None identified as a confirmed, exploitable, live defect in this read-only pass. The closest candidate — potential cross-tenant leakage from a missed `tenant_id` filter somewhere in ~70 service files — is a **structural risk**, not a demonstrated bug (§10); escalate to P0 only if a specific missing filter is found by a targeted follow-up audit of every service method that queries `TenantScopedMixin` tables.

**P1**
- Multi-tenancy has no database-level backstop (no RLS) — a single missed filter anywhere is a full cross-tenant data exposure with no second layer of defense (§10, §20).
- Frontend has zero automated test coverage (§22).
- No CI pipeline exists to catch regressions before merge (§23) — every change to a repo this large currently relies entirely on manual/local test runs.
- AI features are effectively OFF (`DeterministicAIProvider`) in the default/current environment configuration — anything marketed as "AI-powered" needs an `AI_PROVIDER` + API key actually set to be true in a given deployment (§4, §11).

**P2**
- Several integrations users may expect from a "connect anything" experience (Xero, Google Ads, Meta Ads, Google Business, ServiceTitan, Jobber, and true API-based Angi/Thumbtack/Nextdoor) are explicit stubs — real, but disclosed only in code comments, not obviously surfaced to end users in the `/settings/integrations` UI (not verified either way — worth checking that the UI doesn't imply these are live).
- Backend test *pass* status was not verified in this audit (only collection) — recommend an explicit `pytest` run before treating "1,417 tests" as a quality signal.
- Voice AI's realtime engine explicitly does not consume Company Memory (self-documented gap, §19) — a known inconsistency between two "AI-powered" features.

**P3**
- Five very large first-party audit/status markdown docs at repo root risk drifting out of sync with code over time and add cognitive overhead for new contributors; consider consolidating or archiving superseded ones.
- One unresolved "mock" reference in `frontend/app/automations/page.tsx` worth a two-minute direct check.

## 32. Missing Capabilities

Business discovery wizard, cross-category recommendation engine, website/page generation and publishing, a general-purpose configurable AI agent framework, MCP support, ecommerce/product/inventory/order model (blocks dropshipping-style use cases entirely), supplier/provider directory concepts (blocks medical-tourism-style use cases), frontend automated tests, CI pipeline, Postgres RLS, MFA/SSO for end-user login, metrics/tracing observability stack, and any live API integration with Xero, Google Ads, Meta Ads, Google Business Profile, ServiceTitan, or Jobber.

## 33. Existing Capabilities

A deep, coherent, multi-tenant field-service operations platform: CRM, job/work-order lifecycle with QA and completion packets, quoting and internal-attestation contracts, invoicing/AR/collections/refunds/credit-notes/write-offs with approval workflows, job costing/profitability, vendor/subcontractor billing, cash forecasting, marketing (campaigns/content/SEO/outbound/nurture/reactivation/attribution), customer retention (lifecycle, risk signals, reviews, referrals, warranties, service reminders), compliance license tracking, a real RBAC/permission system enforced for both humans and AI actions, a governed 57-tool AI-callable action layer with approval/audit, an AI voice receptionist (Twilio-cascaded and OpenAI-Realtime engines) with guardrails against hallucinated appointment slots and a deterministic emergency-detection override, a pgvector-backed knowledge/RAG layer, a deterministic Morning Brief with optional AI prose polish, real Stripe/QuickBooks/Google Calendar/Twilio integrations, an event bus (Redis Streams or in-memory), and a Temporal-backed durable automation engine.

## 34. Evidence Index

Key file:line references cited throughout this document (non-exhaustive; see inline citations in each section for full detail):
- `backend/app/main.py:24-47` — production secret-enforcement gate
- `backend/app/api/deps.py:20,54,66` — tenant scoping from JWT
- `backend/app/core/security.py:21-140` — JWT token issuance/types
- `backend/app/models/rbac.py:4-80+` — Role/Permission enums
- `backend/app/ai/execution_service.py:1-54` — AI execution boundary, explicit "no autonomous agent" admission
- `backend/app/services/ai_provider.py:1-100` — AI provider abstraction, deterministic fallback
- `backend/app/services/openai_realtime_voice_service.py:1-50+` — governed voice AI function calling
- `backend/app/services/knowledge_retrieval_service.py:1-60` — RAG pipeline
- `backend/app/integrations/stripe_client.py:1,25,40,58,120-349` — real Stripe client
- `backend/app/integrations/marketplace_adapters.py:1-29` — honest stub for Angi/Thumbtack/Nextdoor
- `backend/app/workflows/automation_workflow.py` (full file) — real Temporal wait workflow
- `backend/app/workers/main.py:1-40` — real Temporal worker
- `backend/app/core/rate_limit.py:1-30` — rate limiting design
- `backend/app/core/error_monitoring.py:33-51` — Sentry optional fallback
- `.env.example:1-111` — full config surface and real-vs-stub integration disclosure
- `frontend/lib/api.ts:1-1735+` — frontend API client (real fetch, retry, refresh)
- `backend/app/api/v1/router.py:70-134` — full route mount list
- Directory listings of `backend/app/models/*.py` (208 classes), `backend/app/tools/builtin/*.py` (57 files), `frontend/app/**/page.tsx` (57 pages), `backend/alembic/versions/` (40 migrations) — exhaustive enumeration, not sampled

---

## Final Answers

**1. What is Klaros actually today?** A vertical field-service/home-services operations SaaS (CRM + jobs + quotes/contracts + invoicing/AR + marketing + retention) with a governed AI-tool-calling layer and a real AI voice receptionist bolted onto a deep, mature backend — not a general AI business-builder platform.

**2. What is fully implemented?** Core CRM, operations/job lifecycle, finance/invoicing/AR, quotes/contracts, marketing suite, retention suite, RBAC, multi-tenant data model, the tool-execution/policy/approval/audit framework, Temporal-backed automation engine, event bus, Stripe/QuickBooks/Google Calendar integrations, pgvector RAG pipeline, and the voice AI receptionist.

**3. What is partially implemented?** AI orchestration (three narrow call sites, no central agent), communication breadth beyond Twilio voice, analytics (feature-scoped, not unified), Company Memory (modeled but not consumed by the realtime voice engine), observability (logs + optional Sentry, no metrics/tracing).

**4. What is UI/scaffolding only?** The `AIExecutionService` boundary is explicitly scaffolding for a not-yet-built autonomous agent (its own docstring says so). Some integration settings UI likely lists providers (Xero, Google Ads, etc.) whose backends are stubs — not independently confirmed from the UI side in this pass.

**5. What is missing?** Business discovery, cross-category recommendation engine, website creation, general configurable AI agents, MCP, ecommerce/product/inventory/order model, provider/supplier directories, frontend tests, CI, Postgres RLS, MFA/SSO.

**6. Which integrations actually work (real API calls verified)?** Stripe (directly verified, real HTTP client code read). QuickBooks and Google Calendar (real OAuth2 client code present, not independently re-verified against live APIs but code shape matches genuine SDK usage). Twilio (real client file present, voice path traced). SendGrid/OpenAI/Anthropic stated real in `.env.example` and consistent with `ai_provider.py`'s design, not independently re-verified line-by-line for SendGrid.

**7. What is production-ready?** Core business-operations backend (CRM/ops/finance/marketing/retention), RBAC/tool-policy framework, Stripe billing, and the secret-enforcement boot gate. See §30 for the full per-subsystem grid.

**8. What is broken?** No confirmed broken feature was found in this read-only pass (no P0). One unresolved "mock" string in `automations/page.tsx` warrants a direct look.

**9. What technical debt exists?** No DB-level tenant isolation backstop, no frontend tests, no CI, very large first-party docs that risk drifting from code, and an unusually large "honesty note" documentation overhead embedded throughout the codebase (a strength for auditability, but a sign of a project that has undergone many iterative "phase" corrections).

**10. What would need investigation before changing the architecture?** (a) A full per-service-method tenant-filter audit before trusting multi-tenancy at scale; (b) whether `pytest`'s 1,417 tests actually pass, not just collect; (c) the real depth of `embedding_provider.py`, `knowledge_qa_service.py`, `automation_service.py`, and `docker-compose.yml`'s exact service topology, none of which were fully read in this pass; (d) direct review of `frontend/app/onboarding/page.tsx` and `frontend/app/automations/page.tsx` (mock reference) before any claim about onboarding UX or automation-page correctness.
