# Klaros AI — Final Deployment and CI/CD Architecture

Covers items U (deployment) and V (CI/CD).

## Verified current state

**Docker:** 3 Dockerfiles — `frontend/Dockerfile` (`node:20-slim`, single-stage, `npm install && npm run build`, `CMD npm run start`); `backend/Dockerfile` (`python:3.12-slim`, installs `gcc libpq-dev curl`, `pip install -r requirements.txt`, `uvicorn app.main:app`) used for `backend`/`worker`/`event-worker`; `backend/Dockerfile.combined` (multi-stage, bundles real Temporal server binaries from `temporalio/auto-setup:1.24` plus FastAPI into one image, run via `scripts/start_combined.sh`) — an explicit single-service cost-optimization path for constrained hosting, not what `docker-compose.yml` uses.

**docker-compose.yml (dev/base):** 8 services — `postgres` (`ankane/pgvector:v0.5.1`), `redis` (`redis:7-alpine`), `temporal` (`temporalio/auto-setup:1.24`), `temporal-ui` (`temporalio/ui:2.31.2`), `backend`, `worker`, `event-worker`, `frontend`. **Temporal is genuinely wired into infra** — a real service plus a dedicated worker consuming it (`TEMPORAL_HOST=temporal:7233` set on backend/worker/event-worker), not merely documented.

**docker-compose.prod.yml:** overlay only (no new services) — removes insecure Postgres-password fallback (fails loudly if unset), drops `--reload`, restores the frontend's real prod build/start command. Self-documented in its own header comment as **never exercised against a real Docker daemon** in this development sandbox.

**No deployment manifests exist**: no Kubernetes, no `render.yaml`, no `vercel.json`, no `fly.toml`, confirmed via exhaustive `find`. Deployment today is documented only in prose (`DEPLOYMENT_RUNBOOK.md`, `DOCKER_DEPLOYMENT.md`, not deep-audited in this pass) plus the two compose files.

**Env:** single `.env.example` at repo root, sectioned (Core, Database, Redis, Event-bus transport, Rate limiting, Auth, Integration credential encryption, CORS, Object storage/S3-compatible — not yet implemented, Temporal, Frontend, third-party integrations, AI provider selection, knowledge/RAG embeddings, Voice). Candid comments flag stub-status providers regardless of env vars set.

**Health checks:** `/health` (liveness) and `/ready` (DB connectivity + Alembic-head match + Redis check + credential-encryption-key sanity) are real, non-trivial implementations (`backend/app/main.py:189-318`) — this corrects an earlier "no confirmed /health endpoint" flag in one of the 20 source documents; it exists and is substantive.

**CI: confirmed absent, definitively.** No `.github/`, no `.gitlab-ci.yml`, no `.circleci/`. Exhaustive search for all YAML files outside `node_modules`/`.venv` found only the two compose files. `requirements-dev.txt`'s comment claiming pip-audit is "run manually/in CI only" is stale/aspirational — no pipeline exists to run it.

## Target deployment architecture

**Frontend:** containerized Next.js (existing Dockerfile, prod command already correct in the overlay) behind a CDN/reverse-proxy — platform choice (managed PaaS vs. self-hosted) is explicitly **UNKNOWN — REQUIRES DECISION**, out of this review's scope per the source documents, and not invented here.

**Backend:** the existing `backend`/`worker`/`event-worker` three-process split is correct and should be preserved as three independently scalable deploy units (API traffic, Temporal workflow execution, and event-bus consumption have different scaling profiles) rather than collapsed into one process, except where `Dockerfile.combined`'s single-service pattern is specifically needed for cost reasons on a constrained host — that path should remain a documented alternative, not the default.

**Postgres:** managed Postgres with `pgvector` extension available (the `ankane/pgvector` image is a dev-only convenience — production must confirm the chosen managed provider supports `pgvector`, flagged **UNKNOWN — REQUIRES VERIFICATION** since the provider is undecided).

**Redis:** managed Redis, used for both event-bus Streams transport and rate limiting — namespace isolation between these two uses (and across tenants, per KLAROS_FINAL_SECURITY_MODEL.md §D) needs explicit verification once a specific Redis product is chosen, since key-eviction/TTL policies differ across managed offerings.

**Temporal:** either a managed Temporal Cloud target or a self-hosted `temporalio/auto-setup` deployment matching the existing compose topology — the worker code itself (`app/workers/main.py`) does not need to change between these options, only `TEMPORAL_HOST`.

**Workers:** `worker` (Temporal) and `event-worker` (event-bus) deployed as separate long-running processes/containers, each independently restartable without affecting API availability.

**Object storage:** new deploy unit (MinIO for self-hosted, or a managed S3-compatible service) — currently entirely absent; must ship with encryption-at-rest as an explicit acceptance criterion (KLAROS_FINAL_SECURITY_MODEL.md).

**Website-runtime:** new, isolated deploy unit (separate origin from the main app) serving published `WebsiteVersion` content — isolation limits blast radius of any future website-layer vulnerability from reaching the authenticated app.

**Secrets:** boot-time guards (`JWT_SECRET`, `INTEGRATION_CREDENTIAL_ENCRYPTION_KEY`) already refuse insecure defaults — this behavior must be preserved and the actual secrets-injection mechanism (vault, platform secret manager) is platform-choice-dependent, **UNKNOWN — REQUIRES DECISION**.

**Domains:** the website-runtime's custom-domain-mapping needs a DNS/TLS provisioning flow (e.g. automated cert issuance per published custom domain) — new, not existing today.

**Staging:** currently **absent** — the single highest-priority gap called out consistently across the source documents and reaffirmed here: a staging environment mirroring prod topology is required before any autonomous-agent-tier feature can pass its pre-autonomy security gate (KLAROS_FINAL_SECURITY_MODEL.md), because that gate explicitly requires "at least one staging dry run."

**Migrations:** Alembic, run as a distinct deploy step before the new backend version receives traffic (blue/green or rolling with migration-first ordering) — not yet formalized as a deploy-pipeline step since no CI/CD exists to formalize it in.

**Monitoring/logging/tracing:** `structlog` is present (`structlog==24.4.0` in `requirements.txt`) and `sentry-sdk` is present — both real dependencies, confirming *some* observability tooling exists; this review did not independently verify actual Sentry project configuration or trace-sampling setup, flagged **UNKNOWN — REQUIRES VERIFICATION**.

**Rollback:** container-image-tag rollback (redeploy prior image) plus Alembic's standard downgrade capability for schema — no additional tooling proposed beyond what Alembic already provides, consistent with "do not replace PostgreSQL/Alembic" from `KLAROS_DO_NOT_BUILD_YET.md`.

## CI/CD pipeline (item V) — minimum production-grade, not over-complicated

Confirmed CI is genuinely absent — no prior pipeline to preserve or migrate. Proposed 8-stage pipeline, deliberately matching (not exceeding) the task's suggested shape:

1. **PR opened** → 2. **Lint** (backend: whatever linter config exists — flagged **UNKNOWN — REQUIRES VERIFICATION** whether one is currently configured; frontend: `next lint`, already a defined `package.json` script) → 3. **Typecheck** (`tsc --noEmit` for frontend; Python type-checking tool TBD, not currently configured — **UNKNOWN — REQUIRES DECISION**) → 4. **Unit tests** (backend: existing `pytest` suite, 183 test files, run via existing `pytest.ini` config; frontend: new Vitest suite, see KLAROS_FINAL_TESTING_ARCHITECTURE.md) → 5. **Integration tests** (backend, including the new mandatory tenant-isolation suite, KLAROS_FINAL_SECURITY_MODEL.md) → 6. **Security checks** (`pip-audit`, already present in `requirements-dev.txt` but never actually run anywhere — this pipeline is what finally executes it; `npm audit` for frontend) → 7. **Build** (both Docker images) → 8. **Migration validation** (spin up a throwaway Postgres, run all Alembic migrations head-to-head, confirm no errors — catches the exact class of risk a big-bang migration would introduce) → **Deploy staging** (new environment, see above) → **Smoke tests** (hit `/health`/`/ready` plus a handful of critical-path endpoints) → **Production approval** (manual gate) → **Deploy production**.

**Why this shape and not more:** every stage maps to a concrete, already-identified gap (no lint/typecheck gate today, no automated test run today, no security-scan execution today, no migration-safety check today, no staging today) — nothing speculative is added. This is the pipeline that would have caught, mechanically, the kind of drift this whole review exists to catch manually.
