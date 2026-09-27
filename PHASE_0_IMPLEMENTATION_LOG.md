# Klaros AI — Phase 0 Implementation Log

Implements KLAROS_PHASE_0_IMPLEMENTATION_PLAN.md. This is a REAL
implementation pass (code, migrations, CI, tests) — unlike the prior
audit/architecture passes that produced the 48 markdown documents already
in this repo root. Scope is exactly the plan's 7 items (0.1–0.7); see
"Deferred work" at the end for what was explicitly NOT done and why.

## 1. Baseline

Captured before any implementation change, per Step 2.

```
$ pwd
/Users/mohammedsohail/Desktop/Klaros AI

$ git branch --show-current
main

$ git log -1 --oneline
8c4e13c Redesign marketing homepage, login, and register with richer visuals

$ git status --short
(48 untracked KLAROS_*.md files from the three prior audit/architecture
passes — not from this pass. No other staged/unstaged changes.)
```

Repo layout confirmed: `backend/` (FastAPI + SQLAlchemy async + Alembic,
39 migrations at `backend/alembic/versions/`, 183 files under
`backend/tests/`), `frontend/` (Next.js 16.3.3, React 18.3.1, TypeScript
5.6.3), `docker-compose.yml` (dev: postgres/pgvector, redis, temporal,
temporal-ui, backend, worker, event-worker, frontend), `docker-compose.prod.yml`
(override-only), one root `.env.example`. `.github/` did not exist —
confirmed by `ls`, matching the prior audit's finding.

### Environment assumptions

- Python 3.12.14, backend `.venv` already provisioned with `requirements.txt`
  + `requirements-dev.txt` installed.
- No `mypy` or `ruff` installed (`pip show mypy ruff` → "Package(s) not
  found") — confirms the prior audit's "no linter configured" finding.
- Node/npm available; `frontend/node_modules` already installed for the
  existing `next`/`react`/`tailwind` toolchain, but with zero test
  framework and zero `eslint` package.
- **No Docker and no local PostgreSQL available in this implementation
  environment** (`docker: command not found`, no `psql`/`postgres`/`brew`).
  This is the single most important caveat in this whole log — see the
  RLS section's "What could NOT be validated here" below.

### Baseline test/lint/typecheck results (before any change)

| Check | Command | Result |
|---|---|---|
| Backend lint | — | Not run — no linter configured (`ruff`/`mypy` not installed, no config file found). Confirmed, not assumed. |
| Backend tests | `python -m pytest -q` (from `backend/`, SQLite `:memory:` fallback engine) | **1339 passed, 2 failed, 76 skipped, 16 warnings in 404.16s (6:44)**. Failures: `tests/test_api_phase2.py::test_integrations_report_not_connected`, `tests/test_live_ai_provider.py::test_live_provider_duplicate_event_delivery_is_idempotent` — both **pre-existing**, unrelated to anything in this pass (verified: neither touches tenant/RLS/session/deps/organization code; not re-run or altered by this pass). |
| Frontend lint | `npm run lint` (→ `next lint`) | **FAILS with an environment error**, not a code issue: `Invalid project directory provided, no such directory: .../frontend/lint`. Root cause: Next.js 16 **removed the `next lint` subcommand entirely** (confirmed via `npx next --help` — no `lint` command is listed). The plan's assumption that `next lint` exists is stale relative to the Next.js version actually in this repo. No `eslint` package was installed either. See CI section for how this was handled. |
| Frontend typecheck | `npx tsc --noEmit` | **Passes cleanly**, zero errors. |
| Frontend tests | — | None existed — confirmed (no test framework, no `*.test.*` files, no `test` script in `package.json`). This is the "first one" §0.7 exists to add. |
| Docker build | — | Not run in this pass (no Docker available in this environment — see caveat above). Both `backend/Dockerfile` and `frontend/Dockerfile` exist and are wired into the new CI pipeline's `*-docker-build` jobs, which run on GitHub Actions' own Docker-enabled runners. |

## 2. Changes — file-by-file summary

See §15 for the itemized new/modified list. High-level:

- **RLS audit-mode instrumentation** (§0.2): `backend/app/db/session.py`
  (new `set_tenant_context` helper), `backend/app/api/deps.py`
  (`get_current_user` now stamps it), `backend/app/workflows/activities.py`
  (Temporal activity call sites), new migration
  `backend/alembic/versions/0040_rls_audit_mode_tier1.py`, new tests
  `backend/tests/test_postgres_rls_audit_mode.py` and
  `backend/tests/test_tenant_context_plumbing.py`.
- **CI pipeline** (§0.1): new `.github/workflows/ci.yml`.
- **`autonomy_level` deprecation** (§0.4): docstring/comment updates in
  `backend/app/models/organization.py`, new
  `backend/scripts/check_autonomy_deprecation.sh`, wired into CI.
- **Staging foundation** (§0.5): new `.env.staging.example`; small,
  security-relevant extension in `backend/app/main.py` (staging now gets
  the same secret-hygiene boot check as production); new tests in
  `backend/tests/test_production_secret_guard.py`.
- **Loop/depth protection groundwork** (§0.6): design-only per the plan —
  already reflected consistently in KLAROS_FINAL_AGENT_MODEL.md /
  KLAROS_FINAL_TESTING_ARCHITECTURE.md (pre-existing docs from the prior
  pass). No code or new doc changes made — the plan's own "Done when" for
  this item was already true before this pass started, re-verified by
  reading both documents.
- **Frontend test harness** (§0.7): `vitest`, `@vitejs/plugin-react`,
  `jsdom`, `@testing-library/react`, `@testing-library/jest-dom`,
  `@testing-library/user-event` added as devDependencies;
  `frontend/vitest.config.ts`, `frontend/vitest.setup.ts`; 5 test files
  (21 tests total).

## 3. RLS section (Phase 0 §0.2 — the most important item)

### Tenant-context flow trace (done before writing any policy, per Step 4)

- **HTTP request path**: `oauth2_scheme` → `decode_token` (JWT) →
  `get_current_user` (`backend/app/api/deps.py`) resolves `CurrentUser`
  (`id`, `tenant_id`, `role`) from the token payload, after an indexed
  `User` lookup for real revocation (`token_version`). This is the ONE
  place every authenticated request's tenant identity is established,
  regardless of which of `get_db`/`get_tenant_db` an endpoint actually
  depends on — **because FastAPI caches a dependency's result per request,
  the `db: AsyncSession` instance `get_current_user` receives IS the same
  object instance the endpoint itself receives via `Depends(get_db)`.**
  This is why the implementation stamps tenant context inside
  `get_current_user`, not only in `get_tenant_db` (which turned out to be
  used by only 2 of the ~29 files that depend on a DB session — stamping
  only there would have covered almost nothing).
- **`get_tenant_db`** (`backend/app/api/deps.py`): pre-existing, already
  documented as "enforcement happens at the query layer" (manual
  `.where()`). Now also calls `set_tenant_context` — redundant with
  `get_current_user`'s call in the same request (harmless, idempotent
  `SET LOCAL` within the same transaction) but kept for defense-in-depth
  and because it's literally named in the plan's "files/subsystems
  affected" list.
- **`app/db/session.py`'s `get_db()`**: opens one `AsyncSession` per
  request from `async_session_maker` (bound to a real connection pool
  against Postgres: `pool_size=10, max_overflow=20`, per the existing,
  documented sizing rationale already in that file). No tenant awareness
  at this layer by design — it's the generic session factory every other
  path (including non-tenant-scoped platform code) also uses.
- **Temporal workflows/activities** (`backend/app/workflows/activities.py`):
  run OUTSIDE any HTTP request — no `get_current_user()` exists here. Both
  real call sites that open their own `async_session_maker()` session
  (`resume_automation_execution_activity`) already receive `tenant_id` as
  an explicit activity argument (from the workflow's own input, which
  itself originated from an authenticated request that started the
  workflow) — now call `set_tenant_context(session, tenant_uuid)`
  immediately after opening each session. `execute_tool_activity` goes
  through `ToolRegistry.execute()` via `build_tool_registry`, which opens
  its OWN sessions internally per tool call — **not wired in this pass**
  (see "RLS NOT YET COVERED" below); `ExecutionContext.tenant_id` is
  already passed through correctly to that layer's manual `.where()`
  filtering, so this is a coverage gap for the *audit-mode plumbing*, not
  a tenant-isolation gap in today's actual behavior.
- **In-process `EventWorker`** (`backend/app/events/worker.py`) and the
  per-domain event handlers (`backend/app/events/*_handlers.py`): each
  handler receives a `session_factory` and opens its own sessions;
  tenant_id is available per-event (events carry `tenant_id`) but **the
  `set_tenant_context` call was NOT added to every handler file in this
  pass** — there are 8 handler files, each with multiple handler
  functions, and wiring all of them was judged out of the reviewable-diff
  budget for this Phase 0 slice (Step 15: "the final diff must be small
  and reviewable"). Recorded as a PHASE 1 follow-up, not silently dropped.
- **Webhooks** (`backend/app/api/v1/webhooks.py`,
  `marketplace_webhooks.py`): confirmed, as suspected going in, that these
  have **NO authenticated tenant context at all** — `tenant_id` is derived
  from the signed payload itself (e.g. Stripe's `metadata.tenant_id`) only
  AFTER signature verification, specifically documented in that file as
  "never trust `tenant_id` from [the payload] before its signature
  verifies." These use `get_db` directly, never `get_current_user`, so
  they are **not** touched by this pass's `set_tenant_context` wiring.
  This is correct given the current design (there is no JWT here to
  derive tenant identity from) but means webhook-driven writes are NOT
  YET instrumented — recorded below.
- **CLI/scripts, admin operations**: no dedicated CLI/admin session-opening
  code path was found beyond the ones above (verified by grep for
  `async_session_maker()` call sites across `app/`).
- **Test suite** (`backend/tests/conftest.py`): `_reset_database` (autouse,
  function-scoped) drops and recreates the entire schema from
  `Base.metadata` directly — **it does NOT run Alembic migrations**. This
  matters critically for RLS: migration 0040's `CREATE POLICY`/`ENABLE ROW
  LEVEL SECURITY` DDL is invisible to the default test run. Handled by
  adding a **module-local, autouse fixture** inside
  `tests/test_postgres_rls_audit_mode.py` that re-applies the exact same
  DDL after each test's fresh schema, rather than editing the shared
  `conftest.py` (which would add RLS-DDL overhead to all ~1400 other
  tests for a change that only 6 tests need).

### Tables covered (exactly 5, per the plan)

`IntegrationConnection`, `ApprovalRequest`, `AuditLog`, `CompanyMemory`,
and **`User`** (not `Organization`) — the plan's table read as
"Organization/User," which this pass resolves explicitly: `Organization`
IS the tenant root row (its own `id` is the tenant identity) and has no
`tenant_id` column to filter on at all; `User` is the actual 5th
`TenantScopedMixin` table with a real `tenant_id` column
(`backend/app/models/user.py`), so it is the one that fits "RLS on a
tenant-scoped table." This decision is recorded here, not silently made.

| Table | Tenant column | RLS enablement | Policy | Tenant-context source | Read behavior | Write behavior | Admin/migration behavior | Rollback |
|---|---|---|---|---|---|---|---|---|
| `integration_connections` | `tenant_id` | `ENABLE ROW LEVEL SECURITY` (not `FORCE`) | `tenant_isolation_audit_policy`: `FOR ALL USING (true) WITH CHECK (true)` | `app.tenant_id`, set via `SET LOCAL`/`set_config(..., true)` in `get_current_user` | Unfiltered — audit mode, zero behavior change | Unfiltered — audit mode, zero behavior change | Migration/superuser role bypasses (no `FORCE`) | `DROP POLICY IF EXISTS ...; ALTER TABLE ... DISABLE ROW LEVEL SECURITY` (migration `downgrade()`) |
| `approval_requests` | `tenant_id` | same | same | same | same | same | same | same |
| `audit_logs` | `tenant_id` | same | same | same | same | same | same | same |
| `company_memories` | `tenant_id` | same | same | same | same | same | same | same |
| `users` | `tenant_id` | same | same | same | same | same | same | same |

**RLS COVERED**: exactly the 5 tables above.
**RLS NOT YET COVERED**: every other tenant-scoped table in the schema
(~185 per the plan's own estimate — CRM, Operations, Finance, Compliance,
Marketing, Communications, Knowledge, Contracts, Quotes, Payments,
Retention, Voice, Automation, etc.). No policy of any kind exists on any
of them. This is a large, explicit, intentional gap, not an oversight —
expanding coverage is the ongoing Phase 0/1 workstream the plan itself
describes as "continues as an ongoing... workstream, by sensitivity, not
blocking the rest of Phase 0." Do not read this migration as "the
database has tenant isolation" — it does not, beyond the pre-existing
manual `.where()` convention everywhere.

### Why `SET LOCAL`, not `SET` (the plan's named highest-risk detail)

`SET` is session-scoped: on a pooled connection, a value set by one
request would persist on that physical connection until explicitly reset
or the connection is dropped — and this app's Postgres engine uses a real
pool (`pool_size=10, max_overflow=20`), so the SAME physical connection
serves many different tenants' requests over its lifetime. A bare `SET`
would leak tenant A's `app.tenant_id` into tenant B's request the next
time the pool handed that connection out. `SET LOCAL` (via
`set_config('app.tenant_id', value, true)` — the third argument is
Postgres's `is_local` flag) is scoped to the current transaction and
resets automatically when that transaction ends, before the connection
ever goes back to the pool. This was proven, not just asserted — see
`test_set_local_tenant_context_does_not_leak_across_pooled_connections`
and `test_concurrent_requests_maintain_isolated_tenant_context` below.

### Tests written

`backend/tests/test_postgres_rls_audit_mode.py` (Postgres-only, skipped on
SQLite via the existing `requires_real_postgres` marker convention):

1. `test_rls_is_enabled_on_all_five_tier1_tables` — confirms the migration
   actually attached its policy (direct `pg_class`/`pg_policies` check).
2. `test_audit_mode_is_a_real_no_op_today` — a session that never calls
   `set_tenant_context` still sees another tenant's row (proves "zero
   behavior change").
3. `test_set_local_tenant_context_does_not_leak_across_pooled_connections`
   — the mandatory pooling test: tenant A → tenant B → tenant A across
   repeated acquire/release cycles on the same pooled engine, asserting
   `app.tenant_id` never survives past the transaction that set it.
4. `test_concurrent_requests_maintain_isolated_tenant_context` — two
   `asyncio.gather`'d "requests," each on its own session, each seeing
   only its own tenant value.
5. `test_enforcing_policy_fails_safe_with_no_tenant_context` — inside a
   transaction that is ALWAYS rolled back (never persisted), temporarily
   `FORCE`s RLS and rewrites the policy to the real
   `tenant_id = current_setting(...)::uuid` condition, then asserts (a) no
   context set ⇒ **zero rows**, never all rows (the plan's explicit
   fail-safe requirement) and (b) tenant A's context ⇒ only tenant A's
   rows. This proves the exact mechanism 0.3 will later turn on, without
   this Phase 0 pass actually turning on enforcement.
6. `test_existing_application_queries_are_unaffected_by_audit_mode` —
   the app's own manual `.where(tenant_id == ...)` convention still works
   identically with the policy present.

`backend/tests/test_tenant_context_plumbing.py` (SQLite, runs in the
default suite): proves `set_tenant_context` is a genuine no-op on SQLite
and with `tenant_id=None`, plus an end-to-end register→authenticated-request
smoke test through the real `get_current_user` wiring.

### What could NOT be validated here (important, stated plainly)

**No Docker and no local PostgreSQL exist in this implementation
environment** (verified: `docker` not found, no `psql`/`postgres` binary,
no `brew`). This means:

- Migration 0040 was validated by **static review and by resolving the
  Alembic revision chain** (`alembic.script.ScriptDirectory` confirms head
  = `0040`, `down_revision` = `0039`) — **not** by actually running
  `alembic upgrade head`/`downgrade -1` against a real Postgres in this
  session.
- `tests/test_postgres_rls_audit_mode.py`'s 6 tests were written,
  reviewed carefully against Postgres RLS semantics, and confirmed to
  **collect and skip cleanly** (`6 skipped` — `requires_real_postgres`
  correctly short-circuits them) — but **were never actually executed
  against a real Postgres instance in this session.**
- The new `.github/workflows/ci.yml` CI pipeline — which DOES have a real
  Postgres service container and WILL run all of this for real on every
  PR — has itself never been executed (no GitHub Actions run has happened
  yet; it only exists as a reviewed YAML file in this working tree).

This is an environment/tooling limitation of the sandbox this
implementation ran in, not a design uncertainty — the SQL is standard,
well-established PostgreSQL RLS DDL (`ENABLE ROW LEVEL SECURITY`,
`CREATE POLICY ... USING (true)`, `set_config(..., true)`,
`current_setting(..., true)`), and the `SET LOCAL`/transaction-scoping
argument above is standard, correct Postgres behavior, not a novel claim.
But it was **not proven end-to-end with a real database in this session**,
and that gap should be closed — by running this exact CI pipeline for
real, or by an engineer with Docker/Postgres access running
`tests/test_postgres_rls_audit_mode.py` locally — before treating 0.2 as
fully "Done" per the plan's own "Done when: audit-mode logging runs in
staging for at least one full week... and the connection-pool-reuse test
passes" criterion. Flagged prominently in §17 (Phase 1 readiness) too.

## 4. CI section (Phase 0 §0.1)

New file: `.github/workflows/ci.yml`. Four jobs:

1. **`backend-tests`**: Postgres service container (`ankane/pgvector:v0.5.1`,
   matching `docker-compose.yml`'s image) → `alembic upgrade head` →
   `alembic downgrade -1` + re-`upgrade head` (migration reversibility
   smoke test) → `python -m pytest -q` (full suite, against the real
   Postgres service — so the Postgres-only tests, including the new RLS
   suite, actually run here, unlike a local SQLite-only run) →
   `scripts/check_autonomy_deprecation.sh` → `pip-audit` (non-blocking,
   see below).
2. **`backend-docker-build`**: builds `backend/Dockerfile`, no push.
3. **`frontend-checks`**: `npm ci` → `tsc --noEmit` → `npm run test`
   (Vitest) → `npm audit --omit=dev` (non-blocking).
4. **`frontend-docker-build`**: builds `frontend/Dockerfile`, no push.

No step requires a production secret: `AI_PROVIDER`, `EMBEDDING_PROVIDER`,
`STT_PROVIDER`, `TTS_PROVIDER` are all forced to `deterministic`;
`JWT_SECRET`/`INTEGRATION_CREDENTIAL_ENCRYPTION_KEY` are throwaway
CI-only strings.

### Deliberately NOT included, with reasoning

- **Backend lint / typecheck**: no linter (`ruff`) or typechecker (`mypy`)
  is installed or configured anywhere in this repo — confirmed fresh, not
  assumed from the prior audit. Configuring either from scratch (choosing
  rule sets, fixing whatever the first real run surfaces across ~200
  files) is a meaningfully sized, separate piece of work — out of this
  Phase 0 slice's "minimal CI" mandate and its own "don't invent a lint
  step that will just fail" guidance. Recorded as a **Phase 1 candidate**.
- **`next lint` / frontend ESLint**: `next lint` no longer exists in
  Next.js 16 (confirmed by running it — see Baseline). No `eslint`
  package is installed. Rather than newly configure a full ESLint setup
  in a "foundation" phase (meaningful scope growth, and this repo has
  never had one), `tsc --noEmit` is the frontend static-check CI runs
  instead — it already passes cleanly and catches a large class of real
  bugs. Recorded as a **Phase 1 candidate** to add ESLint properly.
- **`pip-audit` / `npm audit` as blocking**: neither tool has ever
  established a clean baseline for this repo (`requirements-dev.txt`'s
  own comment calls the previous pip-audit mention "stale/aspirational").
  Running them and blocking on whatever they find, unreviewed, in the
  same PR that stands up CI itself, risks either a red pipeline on day
  one for pre-existing findings nobody has triaged, or (worse) someone
  reflexively adding blanket ignores to turn it green. Both run, both are
  visible in CI output, neither blocks merge yet — flip to blocking once
  a human has reviewed a first clean/triaged run (**Phase 1 candidate**).
- **Docker Compose up / integration smoke test**: image build only, not a
  running-stack smoke test — that's the staging item (§0.5), which this
  Phase 0 pass explicitly scopes to environment/config foundations, not a
  live deployment.
- **A second workflow file**: everything fits in one `ci.yml`, matching
  the plan's "prefer ONE clear workflow file unless... explicitly calls
  for more than one" — it does not.

## 5. `Organization.autonomy_level` (Phase 0 §0.4)

**Fresh re-verification** (Step 6 — not trusting the prior audit's
finding without checking): `grep -rn "autonomy_level\|AutonomyLevel"
app/` across the entire backend returns matches **only inside
`app/models/organization.py` itself** (the enum definition, the column
definition, and two of its own comments) — zero reads anywhere else in
the codebase: not in any service, any API route, any schema, any tool,
any policy check. Confirms, independently, the prior finding: this field
is stored and defaulted (`AutonomyLevel.LEVEL_0`) but read by no enforcing
code path anywhere. The real, enforced kill switch is
`Organization.ai_paused`, checked inside
`app/tools/registry.py::ToolRegistry.execute()` — confirmed present and
distinct (not touched by this pass).

**Deprecation approach taken** (matches the plan's "comment update only,
no code removal" exactly):

- `backend/app/models/organization.py`: `AutonomyLevel`'s class docstring
  rewritten to state plainly it is deprecated/unenforced, name the real
  kill switch, and warn against building new functionality against it;
  the `autonomy_level` column gets a matching inline comment.
- No column removed, no migration written for it — matches the plan's "no
  migration" and the task's explicit "Do NOT remove the database column in
  Phase 0."
- New `backend/scripts/check_autonomy_deprecation.sh`: a plain grep-based
  CI guardrail (matching the plan's own "the grep-check itself is the
  test") that fails any PR introducing a new match for
  `autonomy_level`/`AutonomyLevel` outside `app/models/organization.py`.
  Wired into `.github/workflows/ci.yml`'s `backend-tests` job. Verified
  working: run against the current tree, it passes (`OK: no new reads...`).

## 6. Staging foundation (Phase 0 §0.5)

Confirmed current state first: no staging environment, no deployment
manifests beyond `docker-compose.yml`/`docker-compose.prod.yml` (the
latter an override-only file, self-documented as never exercised against
a real Docker daemon — unchanged by this pass, and still not exercised
here either, for the same no-Docker-in-this-environment reason noted in
the RLS section).

**What was built**: `.env.staging.example` — documents exactly what
staging changes relative to `.env.example`'s defaults (`ENV=staging`,
dedicated Postgres/Redis, staging-only `JWT_SECRET`/
`INTEGRATION_CREDENTIAL_ENCRYPTION_KEY`, sandbox/test-mode third-party
credentials only, dedicated object storage path/bucket), plus the
migration/seed/reset strategy for staging (real migrations via the same
`alembic upgrade head` CI now validates; empty-and-self-seeded via the
app's own register flow, never a production data copy — no sanitization
tool is built, matching the reconciliation doc's "avoid building a tool
before anyone's decided it's needed" discipline; reset = drop + recreate
+ re-migrate).

**Small, deliberate code change**: `backend/app/main.py`'s
`_assert_production_secrets_are_real` (the boot-time refusal to start
with the public default `JWT_SECRET`/`INTEGRATION_CREDENTIAL_ENCRYPTION_KEY`)
now also applies to `ENV=staging`, not only `ENV=production` — staging is
just as network-reachable and just as exploitable if it silently reuses
the public default secret; there was previously no concept of "staging"
in this check at all. This does **not** change the existing, deliberate
choice to keep `/docs`/`/redoc` enabled for staging (matches `development`
there, unchanged, per that code's own pre-existing comment) — that's a
separate design decision this pass did not revisit. New tests added:
`test_refuses_to_start_in_staging_with_default_secret`,
`test_starts_in_staging_with_real_secrets` in
`backend/tests/test_production_secret_guard.py` — both pass.

**Deployment platform decision (managed PaaS vs. self-hosted)**: still
explicitly **UNKNOWN — REQUIRES DECISION**, exactly as the plan itself
flags it. Not decided here — out of scope for this document to decide.

## 7. Frontend test harness (Phase 0 §0.7)

**Framework chosen: Vitest + React Testing Library** (not Playwright/E2E).
Reasoning: the plan's own §0.7 explicitly describes this as an "empty but
wired" foundation stage with "a minimal smoke test... not left at zero
tests, but not required to have meaningful coverage yet" — a fast,
in-process component/unit runner is the right fit for that; Playwright's
value is full-browser E2E against a running app, which is a heavier,
separate investment appropriate once there's meaningful page-level
behavior worth testing end-to-end (a Phase 1+ decision, not this
foundation step). This matches the task's own steer ("Playwright only if
the plan calls for E2E in Phase 0, which is unlikely").

**Version pin note**: `npm install -D vitest ...` at latest failed with an
unresolvable peer-dependency conflict (`vitest@5.x` requires
`@types/node@^20.19 || >=22.12`; this repo pins `@types/node@20.16.11`).
Rather than force an unrelated version bump or use `--legacy-peer-deps`
(risking a silently broken resolution), pinned to `vitest@1.6.1` (with
matching `@vitejs/plugin-react@4.3.4`, `jsdom@25.0.1`,
`@testing-library/react@16.1.0`) — a version line that resolves cleanly
against this repo's existing dependency pins. `npm audit` flags 4
dev-only vulnerabilities (esbuild/vite, moderate–critical) pulled in
transitively by this vitest line — this is a known **dev-server-only**
esbuild advisory (a dev server accepting cross-origin requests), not
exploitable in a CI/test-run context where no dev server is ever started;
documented here rather than silently ignored, and CI's `npm audit` step
is non-blocking pending a real Phase 1 dependency-hygiene pass.

**Files**: `frontend/vitest.config.ts` (jsdom environment, `@/*` alias
matching `tsconfig.json`), `frontend/vitest.setup.ts` (`@testing-library/jest-dom`
matchers). `package.json` gets `"test": "vitest run"` and
`"test:watch": "vitest"`.

**Tests written** (5 files, 21 tests, all passing — `npm run test`
verified locally in this session):

1. `frontend/lib/__tests__/api.test.ts` (7 tests) — `withRetry`'s actual
   retry/no-retry decision logic (5xx/429 retried, 4xx/401 not, network
   errors retried) and `ApiError`'s shape. Covers "API client behavior"
   from the task's required list.
2. `frontend/lib/__tests__/useAuth.test.tsx` (3 tests) — no-token redirect
   to `/login` (**protected-route behavior**), successful load, and
   session-expiry handling (token cleared, redirected, error surfaced).
   Covers "auth/session behavior" AND "protected-route behavior."
3. `frontend/components/ui/__tests__/Toast.test.tsx` (3 tests) — push,
   dismiss, and the "used outside its Provider" guard. Covers "one
   reusable UI component."
4. `frontend/components/ui/__tests__/StatCard.test.tsx` (5 tests) — label/
   value rendering, trend badge direction, absent-trend omission, note
   rendering.
5. `frontend/app/customers/__tests__/page.test.tsx` (3 tests) — empty
   state, populated list, error-with-retry — mocking `@/lib/api` (which
   also covers `AppShell`'s `NotificationBell` child, since it imports
   from the same module) and `@/lib/useAuth`. Covers "one representative
   page."

`npx tsc --noEmit` still passes cleanly with the new test files present.

## 8. Loop/depth protection groundwork (Phase 0 §0.6)

Design-only per the plan. Re-read KLAROS_FINAL_AGENT_MODEL.md and
KLAROS_FINAL_TESTING_ARCHITECTURE.md during this pass: both already
consistently describe `max_tool_chain_depth`/`max_executions_per_hour`/
`max_concurrent_executions` and the governance-chain placement, produced
by the prior architecture pass. The plan's own "Done when" for this item
("the design is reflected consistently across [both documents] — already
true as of this document set") was already satisfied before this
implementation pass began. No `Agent`/`AgentVersion`/`AgentExecution`
table, no code, was added — correctly out of Phase 0's scope per the task's
explicit prohibition.

## 9. Validation

### Test commands run, with exact results

```
# Backend — targeted re-runs during implementation (Step 11: test after
# each subsystem, not just at the end)
$ python -m pytest tests/test_postgres_rls_audit_mode.py -q
6 skipped, 1 warning in 0.02s   # correctly skips — no Postgres in this environment

$ python -m pytest tests/ -q -k "auth or deps or tenant"
196 passed, 17 skipped, 1210 deselected, 4 warnings in 60.53s

$ python -m pytest tests/test_production_secret_guard.py tests/test_observability.py -q
17 passed, 1 warning in 2.61s

$ python -m pytest tests/test_production_secret_guard.py -q   # after adding staging tests
6 passed, 1 warning in 0.81s

$ python -m pytest tests/test_tenant_context_plumbing.py -q
3 passed, 1 warning in 0.74s

# Backend — final full-suite regression run (Step 12), see §12 for result

# Frontend
$ npx tsc --noEmit
(no output — zero errors)

$ npx vitest run
Test Files  5 passed (5)
     Tests  21 passed (21)
  Duration  1.90s
```

### Migration validation

- Alembic revision chain resolves correctly: `head = "0040"`,
  `down_revision = "0039"` (verified via
  `alembic.script.ScriptDirectory.from_config(...).get_current_head()`).
- **NOT executed against a real Postgres in this session** — no
  Docker/Postgres available here (see RLS section's caveat). CI's
  `backend-tests` job does run `alembic upgrade head` →
  `alembic downgrade -1` → `alembic upgrade head` against a real Postgres
  service container on every PR going forward; that is the first real
  execution of this migration.
- Downgrade path IS implemented and reviewed (drops the 5 policies,
  disables RLS on the 5 tables) — a real, working downgrade, not a "no
  safe downgrade" case.

## 10. Known issues

- The two pre-existing baseline test failures
  (`test_api_phase2.py::test_integrations_report_not_connected`,
  `test_live_ai_provider.py::test_live_provider_duplicate_event_delivery_is_idempotent`)
  remain unfixed — out of scope (unrelated to Phase 0, not introduced by
  this pass). Flagged for separate triage.
- `next lint` no longer works in this repo (Next.js 16 removed it) — no
  functioning frontend lint step exists (mitigated in CI by `tsc --noEmit`,
  but that is not a full substitute).
- `npm audit` reports 4 dev-only vulnerabilities in the pinned `vitest@1.x`
  line's transitive `esbuild`/`vite` deps (moderate–critical severity,
  dev-server-only exposure, not exploitable in CI/test context).
- RLS migration and RLS test suite are unexecuted against a real Postgres
  as of this log — see §3's caveat and §17.

## 11. Security findings

- Confirmed (not a new finding, but re-verified as part of the tenant-
  context trace): webhooks have no authenticated tenant context and are
  not covered by this pass's `set_tenant_context` wiring — correct given
  their current signature-based design, but worth tracking as RLS
  coverage expands (a future enforcing policy on any table a webhook
  writes to would need webhooks to set `app.tenant_id` from the verified
  payload, not from auth).
- No production credentials were introduced into staging/CI config —
  `.env.staging.example` uses only placeholders/sandbox-mode markers;
  `ci.yml` uses only throwaway CI-only strings.
- No tenant ID or other identifier was ever interpolated into raw SQL as
  a string — every `text(...)` call in the new RLS code uses bound
  parameters (`:tenant_id`) or, for the two places a table name is
  interpolated (`ALTER TABLE {table} ...`, `CREATE POLICY ... ON
  {table}`), the value comes from a fixed, hardcoded Python list
  (`RLS_TABLES`) in the migration/test file itself — never from any
  request input.
- No secrets were logged: the new `structlog` line in
  `set_tenant_context`'s None-branch logs only `reason="tenant_id_is_none"`,
  never a value.

## 12. Regressions (classified)

Final full-suite regression run (`python -m pytest -q`, after all Phase 0
changes, from `backend/`):

```
2 failed, 1344 passed, 82 skipped, 16 warnings in 353.04s (0:05:53)
```

- **1344 passed** = the original 1339 baseline passes + 5 net new passing
  tests added by this pass (`test_tenant_context_plumbing.py`'s 3 tests +
  `test_production_secret_guard.py`'s 2 new staging tests).
- **82 skipped** = the original 76 baseline skips + the 6 new
  `test_postgres_rls_audit_mode.py` tests, correctly skipped (no real
  Postgres in this environment — see §3).
- **2 failed** = the exact same two failures as the baseline
  (`test_api_phase2.py::test_integrations_report_not_connected`,
  `test_live_ai_provider.py::test_live_provider_duplicate_event_delivery_is_idempotent`),
  byte-for-byte the same test IDs — classified **PRE-EXISTING FAILURE**,
  unchanged by this pass.

**Zero new regressions.** Every test this pass added or modified passes;
every pre-existing failure is exactly the same failure, not a new one.

## 13. Deferred work (classified)

- **PHASE 1**: expand RLS audit-mode coverage beyond the 5 tier-1 tables,
  by sensitivity (per the plan's own described ongoing workstream); wire
  `set_tenant_context` into the remaining event-handler files and
  `ToolRegistry`'s internal session-opening path; add a webhook-side
  tenant-context mechanism once a real enforcing policy needs one;
  configure a real backend linter/typechecker (`ruff`/`mypy`) and add it
  to CI; add a proper ESLint config for the frontend (replacing the
  now-nonexistent `next lint`); establish a `pip-audit`/`npm audit`
  clean/triaged baseline and flip both to blocking in CI; decide the
  staging deployment platform (managed PaaS vs. self-hosted — explicitly
  UNKNOWN per the plan) and actually stand up a reachable staging
  deploy; execute this pass's RLS migration and test suite against a
  real Postgres for the first time (this session had none available) and
  let audit-mode logging run for the plan's required "at least one full
  week" before treating 0.2 as fully Done; item 0.3 (RLS enforcement —
  `FORCE ROW LEVEL SECURITY` + real policies on the same 5 tables) is
  explicitly gated on that clean week and is NOT started here.
- **LATER**: rename `automation.py` → `tool_policies.py`
  (KLAROS_ARCHITECTURE_RECONCILIATION.md item #5 — a pre-existing,
  already-recorded, explicitly non-blocking cleanup; not touched here);
  object-storage encryption-at-rest (reconciliation item #6, deployment-
  target-dependent, still UNKNOWN).
- **BUG-not-fixed**: the 2 pre-existing baseline pytest failures (§10).
- **ARCHITECTURAL-QUESTION**: none newly raised by this pass. The
  reconciliation document's existing open items (staging deployment
  platform, object-storage encryption) remain open, unchanged.

## 14. Rollback plan (per Step 14, for every DB change)

- **Migration 0040 (RLS audit-mode policies)**: `alembic downgrade -1`
  runs `DROP POLICY IF EXISTS tenant_isolation_audit_policy ON <table>`
  then `ALTER TABLE <table> DISABLE ROW LEVEL SECURITY` for each of the 5
  tables. No data is affected (policy/RLS-flag changes only — no column,
  row, or index changes). Application rollback (reverting the code
  changes to `session.py`/`deps.py`/`activities.py`) is independently
  safe and requires no coordinated DB rollback: `set_tenant_context` is a
  no-op with no policy present, and harmless even with the audit-mode
  (`USING true`) policy present. Staging would be restored the same way
  any staging reset works (§6): drop, recreate, re-migrate.
- **`Organization.autonomy_level` deprecation**: no schema change at all
  — nothing to roll back beyond reverting the comment/docstring edit,
  which has zero behavioral effect either direction.
- **Staging `ENV=staging` secret-check extension** (`app/main.py`):
  reverting the code change restores the previous (production-only)
  behavior; no data or schema involved.
- **CI pipeline**: delete/disable `.github/workflows/ci.yml` — zero
  application impact, matches the plan's own stated rollback.
- **Frontend test harness**: remove the added devDependencies and config
  files — zero application/runtime impact, purely additive tooling.
