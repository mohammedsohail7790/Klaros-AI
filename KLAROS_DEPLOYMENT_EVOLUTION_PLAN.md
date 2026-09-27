# Klaros — Deployment & CI/CD Evolution Plan

Status: architecture proposal only. No `docker-compose.yml`, `docker-compose.prod.yml`, Dockerfiles, or any deployment configuration was modified to produce this document.

## 1. Current state (verified)

Source: direct read of `docker-compose.yml` and `docker-compose.prod.yml` at repo root (verification pass, 2026-09-23).

`docker-compose.yml` (dev) defines 8 services:

| Service | Image/build | Purpose | Depends on |
|---|---|---|---|
| `postgres` | `ankane/pgvector:v0.5.1` | Primary DB, pgvector ext. | — |
| `redis` | `redis:7-alpine` | Event bus transport, rate limiter | — |
| `temporal` | `temporalio/auto-setup:1.24` | Temporal server (`DB=postgres12`, points at `postgres`) | `postgres` (healthy) |
| `temporal-ui` | `temporalio/ui:2.31.2` | Temporal web UI | `temporal` |
| `backend` | `build: ./backend`, `uvicorn app.main:app --reload` | FastAPI API | `postgres`, `redis` (healthy) |
| `worker` | same image as backend, `python -m app.workers.main` | Temporal workflow worker (JobLifecycle, InvoiceOverdue, LeadQualification, EventProcessing) | `postgres`, `redis` (healthy), `temporal` (started) |
| `event-worker` | same image as backend, `python -m app.events.worker` | Redis Streams event-bus consumer, fans out to domain handlers | `postgres`, `redis` (healthy) |
| `frontend` | `build: ./frontend`, `npm run dev` | Next.js app | `backend` |

`docker-compose.prod.yml` is an **override file, not a standalone stack**: it changes only `postgres.environment.POSTGRES_PASSWORD` (made required, no insecure default), `backend.command` (drops `--reload`), and `frontend.command` (`npm run build && npm run start`). It introduces no new services — the production topology is the same 8 services with prod-safe commands and a required DB password. The file's own header states it has not been exercised against a real Docker daemon in this repo's history (self-documented, unverified-in-practice).

**No CI**: no `.github/workflows` for this project (only a vendored dependency's own workflows appear under `backend/.venv`, not this repo's). No `vercel.json`/`render.yaml`. Deployment target as evidenced is self-hosted Docker Compose, not a managed PaaS.

**Object storage**: `backend/app/storage/factory.py` returns the real `LocalFilesystemStorageAdapter` (disk-backed, tenant-namespaced, 25MB size cap, content-type allowlist, path-traversal guard) when `OBJECT_STORAGE_ENDPOINT` is unset, and a `NotConnectedObjectStorageAdapter` (every method raises) when it is set — confirmed genuinely unimplemented, not just unconfigured: no S3 SDK dependency exists in `backend/requirements.txt` at all.

**Secrets**: JWT secret and `INTEGRATION_CREDENTIAL_ENCRYPTION_KEY` both refuse to boot in production with default/unset values (`backend/app/main.py:24-47`) — a real control already in place, to be preserved as-is.

## 2. Target deployment topology

New processes required by the target architecture, additive to the 8 existing services, none of which are removed:

| New/changed service | Why | Runs on |
|---|---|---|
| `backend` (existing) | + new routers: business-discovery, blueprint, recommendation, integration-marketplace, agent, website (`KLAROS_API_EVOLUTION_PLAN.md`) | same process, no new deploy unit |
| `worker` (existing) | + new Temporal workflow definitions: `AgentExecutionWorkflow`, `WebsitePublishWorkflow`, `BusinessLaunchWorkflow` (see `KLAROS_AI_AGENT_ARCHITECTURE.md`, `KLAROS_WEBSITE_BUILDER_SPEC.md`) registered alongside existing 5 workflows | same worker binary, versioned deploys (§5) |
| `event-worker` (existing) | + new domain handlers: `blueprint_handlers.py`, `agent_handlers.py`, `website_handlers.py` | same process |
| **`object-storage`** (new, Phase 0/18) | Real S3-compatible client (MinIO in dev/self-hosted, S3/R2/GCS in cloud) replacing `NotConnectedObjectStorageAdapter`; required before knowledge files, generated website assets, and voice recordings can be tenant-isolated at scale — see `KLAROS_SECURITY_EVOLUTION_PLAN.md` §Object storage isolation | new compose service (`minio` or managed bucket, not self-hosted code) |
| **`website-runtime`** (new, Phase 6) | Serves generated/published tenant websites (Next.js SSG output or a dedicated static file server + edge cache) — kept **separate** from the main marketing `frontend` service so a tenant's generated site is never on the same origin/deploy unit as the Klaros app itself (isolation boundary, not just a nicety — prevents a compromised generated site from having any access to Klaros app cookies/storage) | new compose service / CDN target |
| **CI pipeline** (new, Phase 0) | Backend `pytest`, frontend build + (new) frontend tests, lint, Alembic migration dry-run against a throwaway Postgres+pgvector container, Docker image build | GitHub Actions (no existing workflow to conflict with) |

No change proposed to: `postgres`, `redis`, `temporal`, `temporal-ui` service definitions themselves — the target architecture's new tables/workflows/events are additive to the existing engines, not a reason to swap them (see `KLAROS_DO_NOT_BUILD_YET.md` §1–3).

## 3. CI/CD pipeline design (proposal, no files created)

Recommended stages, in dependency order, for a new `.github/workflows/ci.yml` (not created by this document):

1. **Lint** — `ruff`/`flake8` (backend, tool TBD — not currently configured, **UNKNOWN — REQUIRES VERIFICATION** whether a linter config exists), `next lint` (frontend, already a defined script).
2. **Backend unit + integration tests** — `pytest` against SQLite (fast) and, on a nightly/pre-merge-to-main job, against a real Postgres+pgvector service container (to catch pgvector-specific query paths the SQLite fallback doesn't exercise — `knowledge_retrieval_service.py`'s two-code-path design means SQLite-only CI never tests the HNSW query).
3. **Frontend tests** — currently zero exist (`package.json` has no test script). Phase 0 must add a test runner (Vitest or Jest + React Testing Library — choice not yet made, **UNKNOWN — REQUIRES DECISION**, see `KLAROS_TESTING_STRATEGY.md`) before this stage is meaningful.
4. **Migration dry-run** — spin up a throwaway Postgres+pgvector container, run `alembic upgrade head`, then `alembic downgrade -1` for the newest migration to confirm reversibility, per `KLAROS_DATABASE_EVOLUTION_PLAN.md`'s migration-safety requirement.
5. **Docker build** — build `backend`, `worker`/`event-worker` (shared image per `Dockerfile.combined`), and `frontend` images; fail the pipeline on build error, do not push on PR builds.
6. **Tenant-isolation regression suite** — a dedicated, always-run test tag (e.g. `pytest -m tenant_isolation`) covering every new blueprint/agent/website/marketplace table, modeled on the existing `test_warranties_isolated_per_tenant` pattern the audit found (`KLAROS_AI_CODEBASE_AUDIT.md` §22) — required before any PR touching a new table can merge (see `KLAROS_SECURITY_EVOLUTION_PLAN.md`).
7. **Push + tag** (main branch only) — push versioned images (git SHA tag), do not auto-deploy.

CI does not, at this stage, include auto-deploy to production — deployment ordering/approval remains manual until Phase 11 (Production Hardening) defines a promotion gate. This is a deliberate scope limit, not an oversight: introducing auto-deploy before the tenant-isolation regression suite (item 6) exists would let a cross-tenant leak reach production automatically.

## 4. Environments

| Environment | Purpose | Notes |
|---|---|---|
| **Local dev** | `docker-compose.yml` as-is | unchanged |
| **CI** | ephemeral containers per pipeline run | new, §3 |
| **Staging** (new, Phase 0/11) | mirrors prod topology, separate `DATABASE_URL`/`REDIS_URL`/Temporal namespace, non-production Stripe/QuickBooks/Google/Twilio credentials (test-mode keys) | **does not exist today** — no staging-vs-production separation artifact was found beyond `ENV=development\|production` and `QUICKBOOKS_ENVIRONMENT=sandbox\|production` switches; this is a P0/P1 gap for safely testing agent autonomy and website publishing before they touch real tenant data |
| **Production** | `docker-compose.prod.yml` override, or a managed container platform (ECS/Cloud Run/Fly — **UNKNOWN, not yet chosen**) | secrets via platform secret manager, not `.env` files, once introduced |

## 5. Deployment ordering, worker versioning, migration safety

**Migration-before-code ordering** (must be enforced, not currently automated): Alembic migrations must apply and be backward-compatible with the *previous* release's running code for at least one deploy cycle (additive columns/tables only in the same release as new code that reads them; drops/renames happen in a later, separate release) — this is standard practice but **not currently enforced by tooling**; recommend a migration-lint CI check (e.g. reject a migration that both adds a NOT NULL column without a server_default and is in the same PR as code requiring it) as part of Phase 0.

**Temporal worker versioning**: new workflow definitions (`AgentExecutionWorkflow` etc.) must be registered in the same `worker` deploy as any code path that starts them, and Temporal's own workflow-versioning primitives (`workflow.patched()`) must be used for any change to an *existing* running workflow's logic (e.g. `AutomationWaitWorkflow`) to avoid breaking in-flight executions — this is a Temporal-standard concern, not new to this plan, but must be explicitly adopted once new workflow types are added, since a bad deploy here can strand in-flight automation executions.

**Rollback**: container image rollback (previous tag) is sufficient for `backend`/`frontend`/`worker`/`event-worker` as long as the migration-ordering rule above is followed (old code must still run correctly against the new schema). A schema rollback (`alembic downgrade`) is a distinct, higher-risk action and should be a deliberate manual step, never automatic on a failed health check.

**Health checks**: `docker-compose.yml`'s existing `postgres`/`redis` healthchecks are real; **UNKNOWN — REQUIRES VERIFICATION** whether `backend` itself exposes a `/health` endpoint (not confirmed in either audit pass) — this should be added/confirmed in Phase 0 and used as the deploy-promotion gate for the `backend`/`worker`/`event-worker`/`object-storage`/`website-runtime` services.

## 6. What is explicitly deferred

Per `KLAROS_DO_NOT_BUILD_YET.md`: no change to the underlying engines (Postgres, Temporal, Redis), no move off Docker Compose to a different orchestrator (Kubernetes, Nomad) until a concrete scaling need is demonstrated, no managed-PaaS migration decision made in this document (flagged **UNKNOWN — REQUIRES DECISION**, a business/ops choice outside this architecture analysis's scope).
