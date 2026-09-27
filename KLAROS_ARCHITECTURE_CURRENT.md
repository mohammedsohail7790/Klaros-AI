# Klaros Architecture — Current State

See `KLAROS_AI_CODEBASE_AUDIT.md` §3 for the primary diagram and methodology note. This file expands on it.

## Component map

- **Frontend**: Next.js 16 App Router (`frontend/app/`), React 18, TypeScript, Tailwind. 57 route pages. No server components/data-fetching framework beyond plain `fetch` — a single hand-written client, `frontend/lib/api.ts` (3,835 lines), is the only data-access surface. Auth state via `frontend/lib/useAuth.ts` + `sessionStorage` (access/refresh JWTs).
- **Backend API**: FastAPI (`backend/app/main.py`), 55 routers mounted at `/api/v1` (`backend/app/api/v1/router.py:70-134`). Async throughout (SQLAlchemy 2.0 async + asyncpg).
- **Service layer**: ~70 files in `backend/app/services/` — one service class per domain concept (leads, jobs, invoices, quotes, contracts, campaigns, retention, knowledge, billing, automation, AI provider, embedding provider, etc.). Routers depend on services; services depend on the ORM session, the `ToolRegistry` (when the action is also AI-callable), and the `EventBus` (when the action should publish a domain event).
- **Tool layer**: `backend/app/tools/{base,factory,registry,policy,redact,errors}.py` + 57 files in `backend/app/tools/builtin/`. This is the single execution boundary both a human-triggered service call and an AI-triggered call converge on when the action needs to be AI-callable (not every service method is a tool — many are plain CRUD only reachable via the human REST API).
- **AI boundary**: `backend/app/ai/execution_service.py` — `AIExecutionService.request_tool_execution(...)` is the *only* way anything AI-originated can reach the `ToolRegistry`. No SQL, shell, HTTP, or arbitrary-callable surface is exposed to it.
- **Event bus**: `backend/app/events/{bus,transport,worker,metrics,*_handlers}.py`. Two transports: `InMemoryTransport` (dev/test, single-process) and Redis Streams (`EVENT_TRANSPORT=redis`, production, consumed by a separate `event-worker` Docker Compose service — per `main.py`'s lifespan comments). Domain handlers exist per area: `automation_handlers.py`, `crm_handlers.py`, `finance_handlers.py`, `marketing_handlers.py`, `notification_handlers.py`, `operations_handlers.py`, `retention_handlers.py`.
- **Workflow/durability layer**: real Temporal (`temporalio==1.8.0`). `backend/app/temporal_client.py` (client factory), `backend/app/workers/main.py` (separate worker process polling `TEMPORAL_TASK_QUEUE`), `backend/app/workflows/definitions.py` (`EventProcessingWorkflow`, `InvoiceOverdueWorkflow`, `JobLifecycleWorkflow`, `LeadQualificationWorkflow`), `backend/app/workflows/automation_workflow.py` (`AutomationWaitWorkflow` — used by the Automation Engine for durable waits).
- **Automation engine**: `backend/app/models/automation.py` + `backend/app/services/automation_service.py` — a deterministic trigger (SCHEDULE or EVENT) → condition → action engine, distinct from (but able to use) the Temporal durability layer for waits.
- **Database**: PostgreSQL + `pgvector` extension in production; SQLite (`aiosqlite`) as a real, code-supported second runtime path used primarily by the test suite. 40 Alembic migrations (`backend/alembic/versions/`). No RLS — app-level tenant filtering only (`TenantScopedMixin` + `CurrentUser.tenant_id`).
- **Cache/queue**: Redis — used for the event bus transport, rate limiting (`RATE_LIMIT_BACKEND=redis`), and presumably session/refresh-token bookkeeping (not independently confirmed).
- **Object storage**: local-disk adapter by default (`backend/app/storage/local_adapter.py`, per `.env.example:51-59` — described in-code as "the real, working local-disk adapter... not a mock"); S3-compatible bucket support exists as configuration surface but is explicitly "not implemented yet — reports NOT_CONNECTED" if `OBJECT_STORAGE_ENDPOINT` is set (`.env.example:54`).
- **Integrations**: `backend/app/integrations/` — real clients for Stripe, QuickBooks, Google Calendar, Twilio; `marketplace_adapters.py` for a generic, tenant-configured webhook normalizer (Angi/Thumbtack/Nextdoor); `credential_store.py` for encrypted per-tenant credential storage; `adapters.py`/`base.py` for the shared integration abstraction.
- **Voice**: `backend/app/services/{voice_conversation_service,openai_realtime_voice_service,speech_provider,audio_codec}.py` + `backend/app/api/v1/{voice,voice_stream}.py`. Two selectable engines behind `VOICE_AI_ENGINE`: a cascaded Twilio Media Stream → STT → LLM → TTS pipeline, and an OpenAI Realtime API bridge. Both route every real action through the same governed `AIExecutionService` path and share one tool allowlist.
- **Knowledge/RAG**: `backend/app/services/{knowledge_service,knowledge_retrieval_service,embedding_provider}.py`, `backend/app/models/knowledge.py` (`KnowledgeFile`, `KnowledgeChunk` with a pgvector embedding column, HNSW-indexed per migration 0032).

## What is NOT present architecturally

- No agent runtime / agent registry / configurable-agent object model.
- No MCP server or client.
- No model router beyond a single `AI_PROVIDER=anthropic|openai` switch.
- No message queue product beyond Redis Streams for the event bus (no Kafka/RabbitMQ/SQS).
- No GraphQL layer.
- No Postgres Row-Level Security.
- No API gateway / BFF layer distinct from the FastAPI app itself.
- No CI/CD pipeline definition in this repo.
- No business-discovery, recommendation, or website-generation subsystem.

## Confidence notes

The service-layer and tool-layer file *inventories* above are exhaustive (directory listings). Their *internal correctness* was verified only for the specific files read in the main audit (`ai_provider.py`, `execution_service.py`, `knowledge_retrieval_service.py`, `security.py`, `rate_limit.py`, `error_monitoring.py`, `stripe_client.py`, `marketplace_adapters.py`, `automation_workflow.py`, `workers/main.py`, `deps.py`, `rbac.py`, `organization.py`). The remaining ~60 service files and 50+ tool files were not individually read in this pass — their existence and naming is exhaustive evidence of *scope*, not of *correctness or completeness of implementation* for each one individually.
