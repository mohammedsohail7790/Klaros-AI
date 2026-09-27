# Klaros AI — Phase 0 PostgreSQL Verification

This is the final verification gate for Phase 0. It proves (or disproves) the
claims in `PHASE_0_IMPLEMENTATION_LOG.md` against a REAL PostgreSQL instance —
not SQLite, not mocked Postgres, not static SQL inspection. No implementation
scope beyond Phase 0 was touched (no Business Blueprint / Discovery /
Recommendation / Agent Runtime / Website Builder / Medical Tourism /
Dropshipping / MCP / vertical extensions).

## 1. Verification Status

**VERIFIED WITH LIMITATIONS**

Every Postgres-dependent claim in the Phase 0 log was independently proven
against a real, local PostgreSQL 16.2 instance (not Docker — see §2). RLS
infrastructure, audit-mode policy correctness, tenant-context plumbing,
connection-pool-leak safety, and concurrency isolation are all verified. Two
genuine defects were found and fixed within the allowed scope (a Phase-0 test
bug and a Phase-0 CI/tooling config bug). Several **pre-existing, non-Phase-0**
defects were discovered — for the first time — because this is the first
session in this repo's history where the full backend test suite has ever
actually executed against a real PostgreSQL instance. Those are documented,
not fixed (out of this task's scope), and materially affect whether the CI
pipeline can go green on its first real run. See §17 and §18.

## 2. Environment

| Item | Value |
|---|---|
| PostgreSQL | 16.2 (aarch64-apple-darwin), self-built via the `pgserver` PyPI package (bundles a real Postgres binary) — no Docker/Docker Compose available in this sandbox (`docker`, `colima`, `podman`, `brew` all absent) |
| pgvector | 0.6.2, confirmed via `CREATE EXTENSION vector` + `pg_extension` query |
| Database | `klaros`, dedicated, created for this session only |
| Connection | TCP, loopback-only, dedicated port 5544, isolated data directory (`/tmp/klaros_verify_pgdata`, now removed), never touching the host's unrelated pre-existing Postgres.app instance (a different project, `GCO`, listening on 5432 — left completely untouched) |
| Python | 3.12.14, dedicated venv (`backend/.venv_verify`, created and removed at the end of this session; not left in the repo) |
| Node | v24.20.0 (CI pins Node 20 — see §17) |
| npm | 11.19.0 |
| Docker | **unavailable** — `docker`, `colima`, `podman` all absent from this sandbox. Docker builds and Docker Compose stack tests are BLOCKED (see §13) |
| Test environment | `DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:5544/klaros`, `EVENT_TRANSPORT=memory`, `AI_PROVIDER=EMBEDDING_PROVIDER=STT_PROVIDER=TTS_PROVIDER=deterministic`, `RATE_LIMIT_BACKEND=memory` — mirrors `.github/workflows/ci.yml`'s `backend-tests` job env exactly |

**Important environment-hygiene finding**: `backend/.env` (gitignored, the
developer's own local file) contains real-looking `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, `STRIPE_SECRET_KEY`, `TWILIO_ACCOUNT_SID`/`AUTH_TOKEN`,
and `SENDGRID_API_KEY` values, and `app/core/config.py` loads it automatically
(`SettingsConfigDict(env_file=".env")`). An early full-suite run, before this
was noticed, made real outbound HTTPS calls to `api.stripe.com` (`GET
/v1/balance`, `200 OK`) and `api.twilio.com` (`401`) using those local
credentials — read-only calls, no funds moved, no data mutated, but a real,
unintended third-party API call using someone's live/local credentials
nonetheless. **This is disclosed here, not hidden.** Once noticed, `backend/.env`
was moved aside for the remainder of every test run in this session and
restored byte-for-byte at the end (verified present, unchanged, 905 bytes,
same mtime). All results in this document reflect runs with `backend/.env`
excluded, `DATABASE_URL`/`AI_PROVIDER`/etc. explicitly set to match CI.

## 3. Migration Verification

Ran from `backend/`, real Postgres, exact commands:

```
alembic heads                     → 0040 (head)
alembic upgrade head              → all 40 migrations (0001…0040) applied cleanly
alembic current                   → 0040 (head)
alembic downgrade -1              → 0040 → 0039 (RLS audit-mode downgrade)
alembic upgrade head              → 0039 → 0040 (re-upgrade)
alembic current                   → 0040 (head), confirmed
```

- **Upgrade**: clean, no errors, across the full 40-migration chain (not just
  0040 in isolation).
- **Downgrade**: migration 0040 has a real, working, non-destructive
  downgrade (`DROP POLICY`, `DISABLE ROW LEVEL SECURITY` on all 5 tables) —
  confirmed via direct `pg_class`/`pg_policies` queries before/after (§4).
  No "no safe downgrade" case here; it is fully implemented and it works.
- **Re-upgrade**: schema state after re-upgrade is byte-identical (same RLS
  flags, same single policy per table, same `USING (true) WITH CHECK (true)`)
  to the state immediately after the first upgrade — no drift.
- **pgvector chain**: migrations 0028/0032 (KnowledgeChunk embedding storage,
  upgraded to a real `vector(1536)` column with HNSW index) applied cleanly;
  the `vector` extension is present and functional in this Postgres build.

## 4. RLS Verification

Queried directly against Postgres system catalogs (`pg_class.relrowsecurity`,
`pg_class.relforcerowsecurity`, `pg_policies`) — not inferred from migration
source.

| Table | RLS Enabled | Policy | Audit Mode | Result |
|---|---|---|---|---|
| `integration_connections` | `t` (not forced) | `tenant_isolation_audit_policy`, `FOR ALL`, `USING (true)`, `WITH CHECK (true)` | Yes — permissive, zero enforcement | VERIFIED |
| `approval_requests` | `t` (not forced) | same | same | VERIFIED |
| `audit_logs` | `t` (not forced) | same | same | VERIFIED |
| `company_memories` | `t` (not forced) | same | same | VERIFIED |
| `users` | `t` (not forced) | same | same | VERIFIED |

Exactly one policy per table, `permissive: PERMISSIVE`, `roles: {public}`,
`cmd: ALL` — no unintended broader or narrower policy exists on any of the 5
tables. `relforcerowsecurity = f` on all 5 (confirmed NOT `FORCE`, matching
the audit-mode design — enforcement is explicitly out of scope for Phase 0).
Downgrade correctly returns all 5 tables to `relrowsecurity = f` and zero
policies; re-upgrade correctly restores the exact original state.

## 5. Tenant Context Verification

Trace (verified by direct code read, not assumed):

```
HTTP request → oauth2_scheme → decode_token (JWT)
  → get_current_user() [backend/app/api/deps.py:24]
      - resolves tenant_id from JWT payload
      - calls set_tenant_context(db, tenant_id)   [backend/app/db/session.py:89]
          - SELECT set_config('app.tenant_id', :tenant_id, true)
          - the `true` third arg = SET LOCAL semantics (transaction-scoped,
            not session-scoped)
          - no-op on SQLite, no-op when tenant_id is None (fail-safe: never
            silently sets an empty value a future enforcing policy could
            misread as "no tenant restriction")
  → same AsyncSession instance the endpoint receives via Depends(get_db)
    (FastAPI caches per-request dependency results) → same DB transaction
```

`get_tenant_db()` [`deps.py:70`] redundantly (defense-in-depth) re-stamps the
same value. Temporal activities that open their own sessions
(`app/workflows/activities.py:89,97`) call `set_tenant_context` immediately
after opening each session, using `tenant_id` carried explicitly in workflow
input. Webhooks (`app/api/v1/webhooks.py`) correctly do NOT go through
`get_current_user` — confirmed by direct grep: `tenant_id` there is derived
only from the verified signed payload (e.g. Stripe `metadata.tenant_id`),
never from auth — consistent with the documented design.

Directly tested inside a real transaction:
`SELECT current_setting('app.tenant_id', true)` returns the exact stamped
UUID string immediately after `set_tenant_context()` runs, and reliably
returns `NULL`/`''` once that transaction ends — proven by the dedicated
test suite (§8), executed for real, not just read.

## 6. Connection Pool Verification

**Test**: `test_set_local_tenant_context_does_not_leak_across_pooled_connections`
— 3 full cycles of tenant A → tenant B → tenant A on the same pooled engine
(`pool_size=10, max_overflow=20`, matching `app/db/session.py`'s real
production pool sizing), reading `current_setting('app.tenant_id', true)`
before each session ever calls `set_tenant_context` itself.

**Result**: PASS. In every cycle, a freshly-acquired session (regardless of
which physical connection the pool handed back) saw `NULL`/`''` for
`app.tenant_id` before setting it itself — zero leakage from a prior
borrower's transaction. `SET LOCAL`'s transaction-scoped reset (not a bare
`SET`, which would be session-scoped and would leak) is proven correct, not
just asserted.

## 7. Concurrency Verification

**Test**: `test_concurrent_requests_maintain_isolated_tenant_context` — two
`asyncio.gather`'d "requests," each on its own independently-acquired
session/connection, each setting a different tenant, with an explicit
`asyncio.sleep(0.05)` interleave point to force genuine concurrency (not
false-sequential execution).

**Result**: PASS. Each concurrent coroutine observed only its own
`app.tenant_id` — no cross-talk between simultaneously-open transactions on
the real async SQLAlchemy/asyncpg stack.

## 8. Six RLS Tests (plus the 3 SQLite-side tenant-context-plumbing tests)

All run for real against the live Postgres instance (not read-and-assumed).
Run 3 times back-to-back for determinism — identical result every time.

| Test | File | Result |
|---|---|---|
| `test_rls_is_enabled_on_all_five_tier1_tables` | `test_postgres_rls_audit_mode.py` | PASS |
| `test_audit_mode_is_a_real_no_op_today` | `test_postgres_rls_audit_mode.py` | PASS |
| `test_set_local_tenant_context_does_not_leak_across_pooled_connections` | `test_postgres_rls_audit_mode.py` | PASS |
| `test_concurrent_requests_maintain_isolated_tenant_context` | `test_postgres_rls_audit_mode.py` | PASS |
| `test_enforcing_policy_fails_safe_with_no_tenant_context` | `test_postgres_rls_audit_mode.py` | PASS (after fix — see §16) |
| `test_existing_application_queries_are_unaffected_by_audit_mode` | `test_postgres_rls_audit_mode.py` | PASS |
| `test_set_tenant_context_is_a_no_op_on_sqlite` | `test_tenant_context_plumbing.py` | PASS |
| `test_set_tenant_context_is_a_no_op_when_tenant_id_is_none` | `test_tenant_context_plumbing.py` | PASS |
| `test_get_current_user_stamps_tenant_context_without_raising` | `test_tenant_context_plumbing.py` | PASS |

**9/9 PASS**, both standalone and inside the full 1428-test suite run (zero
failures for these 9 in either context).

## 9. Backend Regression

Two full-suite runs were executed against real Postgres.

**Run 1** (before `backend/.env`'s real third-party/AI credentials were
excluded — see §2's disclosure): `47 failed, 1376 passed, 5 skipped, 16
warnings in 876.59s`. 6 of those 47 failures were confirmed, by targeted
clean re-run, to be caused solely by that credential leakage (5 in
`test_live_ai_provider.py` whose skip condition only checks key *presence*,
not `AI_PROVIDER` routing; 1 in `test_api_phase2.py` whose external-provider
calls hit real Stripe/Twilio because real keys were present).

**Run 2** (clean — `backend/.env` excluded, matching real CI's env exactly):

```
54 failed, 1361 passed, 12 skipped, 16 warnings, 1 error in 811.75s (0:13:31)
```

Every one of the 54 failures + 1 error was individually investigated and
classified — none are new regressions from Phase 0's own diff
(`session.py`/`deps.py`/`activities.py`/`organization.py`/`main.py`/
`test_production_secret_guard.py`):

| Category | Count | Classification | Root cause |
|---|---|---|---|
| `test_postgres_company_memory_{knowledge_qa,marketing,seo}.py::test_alembic_head_unchanged_this_phase` | 3 | **PRE-EXISTING FAILURE**, newly exposed | Hardcoded `assert result == "0034"` — stale from a much earlier phase; would fail identically at any head past 0034, unrelated to migration 0040. These are Postgres-only tests that had never actually executed before this session (no real Postgres was ever available), so this staleness was invisible until now. |
| Knowledge/embedding indexing chain (`test_company_memory_knowledge_qa.py`, `test_knowledge_qa_and_tools.py`, `test_knowledge_retrieval_service.py`, `test_voice_conversation_service.py`, `test_ai_next_action_invoice.py`) | 33 | **PRE-EXISTING FAILURE**, newly exposed | `get_embedding_provider()` (`app/services/embedding_provider.py:178`) constructs `DeterministicEmbeddingProvider()` with its default `dimensions=64`, but the real Postgres `knowledge_chunks.embedding` column is a fixed `vector(1536)` (migration 0032). Every real insert fails: `expected 1536 dimensions, not 64`. A dedicated file (`test_postgres_knowledge_pgvector.py`) already anticipated this and correctly constructs `DeterministicEmbeddingProvider(dimensions=EMBEDDING_DIMENSIONS)` — but the app's own default factory does not, and every test that goes through the real service layer (not the dedicated pgvector test) inherits the bug. This is a real, previously-undetected defect: on SQLite (this repo's only-ever-exercised test engine before this session), `embedding` is a portable JSON column with no dimension enforcement, so it was invisible. |
| `test_openai_realtime_voice_service.py` (whole file) | 18 | **PRE-EXISTING FAILURE**, newly exposed | `OpenAIRealtimeVoiceBridge.open()` (`app/services/openai_realtime_voice_service.py:277`) unconditionally raises if `OPENAI_API_KEY` is falsy — even though these are unit tests using a fully fake/mocked websocket transport that never makes a real network call. `ci.yml` never sets `OPENAI_API_KEY`, so in real GitHub Actions CI this file would fail identically on its first run. It only ever "passed" historically because the developer's local `.env` happened to have a real-looking key present, masking the gap. |
| `test_approval_orchestration.py::test_ai_cannot_call_approve_tool` | 1 (ERROR, not FAILED) | **ENVIRONMENT/STRESS ARTIFACT** | A genuine Postgres `DeadlockDetectedError` during this test's `_reset_database` fixture (`DROP TABLE users`), the very last test of the 1428-test run: `Process 60540 waits for AccessExclusiveLock on relation ...; blocked by process 60692. Process 60692 waits for RowExclusiveLock ...; blocked by process 60540.` Two OS-level connections deadlocking on DDL is consistent with resource/connection-pool contention accumulated from the 51 preceding real failures in the same run (each is a rolled-back transaction). Not reproduced in any of the 3 isolated, repeated runs of the RLS/tenant-context test suite (§8) — i.e., not attributable to the `SET LOCAL`/tenant-context plumbing itself, which was independently verified deadlock-free under its own dedicated pooling and concurrency tests. |

**None of the 54 failures + 1 error touch RLS, tenant-context, staging, CI
YAML, or autonomy-deprecation code** — the actual Phase 0 diff. All are in
pre-existing subsystems (Knowledge/RAG, AI Voice Realtime, an old
company-memory test file) that had simply never been executed against real
Postgres before this verification session.

**The two originally-documented "pre-existing failures"
(`test_api_phase2.py::test_integrations_report_not_connected`,
`test_live_ai_provider.py::test_live_provider_duplicate_event_delivery_is_idempotent`)
were themselves artifacts of the same local-credential contamination** — both
pass/skip cleanly once `backend/.env` is excluded, on both SQLite and real
Postgres. This corrects the implementation log's baseline.

## 10. Frontend Regression

`frontend/node_modules` fresh-installed (`npm ci`, 314 packages).

- `npx tsc --noEmit`: **initially FAILED** (exit 1, 31-line type error) — see
  §16 for the fix. After the fix: **passes cleanly, zero errors**.
- `npx vitest run`, twice for determinism:
  - Run 1: `Test Files 5 passed (5)`, `Tests 21 passed (21)`, 2.13s
  - Run 2: `Test Files 5 passed (5)`, `Tests 21 passed (21)`, 2.39s
  - Deterministic, matches the log's claimed "21 tests, 5 files" exactly.

## 11. CI Verification

**GitHub Actions execution unavailable from this environment** — no real
workflow run was triggered; the statements below are all **local
CI-equivalent commands**, run manually, following `.github/workflows/ci.yml`
step-by-step with its exact env vars.

| CI step | Local result |
|---|---|
| `pip install -r requirements.txt -r requirements-dev.txt` | Clean install, Python 3.12 |
| `alembic upgrade head` | Clean, all 40 migrations |
| `alembic downgrade -1` / `alembic upgrade head` | Clean, matches original post-upgrade state exactly |
| `python -m pytest -q` | **54 failed, 1361 passed, 12 skipped, 1 error** — see §9. **This means `ci.yml`'s `backend-tests` job, as currently configured, would NOT go green on its first real GitHub Actions run** — not because of anything Phase 0 broke, but because of the 3 pre-existing categories in §9, now confirmed for the first time. |
| `./scripts/check_autonomy_deprecation.sh` | PASS — see §14 |
| `pip-audit -r requirements.txt --skip-editable` | 2 known vulnerabilities (`ecdsa` 0.19.2, `PYSEC-2026-1325`) — non-blocking per `ci.yml`'s own `\|\| true`, unchanged/pre-existing |
| `npm ci` | Clean, 314 packages |
| `npx tsc --noEmit` | PASS (after the fix in §16) |
| `npm run test` (Vitest) | PASS, 21/21, twice |
| `npm audit --omit=dev` | **0 vulnerabilities** (was 4 dev-only before the fix in §16 — see below) |
| Backend Docker build | **BLOCKED** — no Docker in this sandbox |
| Frontend Docker build | **BLOCKED** — no Docker in this sandbox |

## 12. Security Audits

- **pip-audit**: 2 known vulnerabilities in `ecdsa==0.19.2` (`PYSEC-2026-1325`), a transitive dependency (via `python-jose`). Pre-existing, not introduced by Phase 0, non-blocking per CI's own design (documented as un-triaged). Not fixed here — out of Phase 0's named scope, and bumping a transitive crypto dependency without review is exactly the kind of unscoped change this task says not to make.
- **npm audit** (full, incl. dev): 4 vulnerabilities (2 moderate, 1 high, 1 critical) — all in the `esbuild`/`vite`/`vite-node`/`vitest` dev-only chain, the same dev-server-only CORS advisory already documented in the implementation log. Unchanged in nature.
- **npm audit --omit=dev** (CI's actual command): **0 vulnerabilities**, confirmed clean (see §16 — improved from the log's originally-reported 4, as a side effect of the `vite` version-pin fix, not a deliberate security pass).

## 13. Docker

**BLOCKED — no Docker, Colima, or Podman available in this sandbox** (`docker`,
`colima`, `podman`, `brew` all confirmed absent via `which`). `backend/Dockerfile`
and `frontend/Dockerfile` both exist and were read directly — both are
ordinary, reasonable single-stage builds (`python:3.12-slim` / a Node base)
with no obvious defect — but neither was actually built. This is an honest
gap, not a claimed pass.

## 14. Autonomy Deprecation

Fresh `grep -rn "autonomy_level\|AutonomyLevel" app/` across the whole
backend: **zero matches outside `app/models/organization.py`** — confirmed
independently, not trusted from the log.

`backend/scripts/check_autonomy_deprecation.sh` tested directly:
- Against current tree: `OK: no new reads of the deprecated autonomy_level field found.` (exit 0)
- With a throwaway injected reference added to `app/services/embedding_provider.py`: correctly fails (exit 1), prints the exact offending lines, then correctly passes again after `git checkout --` reverted the throwaway change. The guard works both ways, verified, not assumed.

## 15. Staging Security

`backend/app/main.py::_assert_production_secrets_are_real` tested directly
(not just read) via `backend/tests/test_production_secret_guard.py`, run for
real:
- `test_refuses_to_start_in_staging_with_default_secret`: PASS — `ENV=staging` + the public default `JWT_SECRET` correctly raises `RuntimeError`.
- `test_starts_in_staging_with_real_secrets`: PASS — real secrets boot cleanly under `ENV=staging`.
- All 4 other tests in the same file (production-only cases, dev-is-unaffected case) also PASS.
- `.env.staging.example` read directly: contains only placeholders/sandbox markers, no real credentials, matches its own stated design (dedicated staging DB/Redis, never a production copy).

## 16. Changes Made During Verification

| File | Change | Why | Test |
|---|---|---|---|
| `backend/tests/test_postgres_rls_audit_mode.py` | `test_enforcing_policy_fails_safe_with_no_tenant_context` rewritten to run its enforcement-check queries as a short-lived, dedicated `NOSUPERUSER NOBYPASSRLS` role (created/torn down around the test) instead of the connecting superuser role, and the throwaway enforcing policy's `USING` clause rewritten from `AND`-chained guards to a `CASE/WHEN` expression | Two real, distinct correctness bugs found while running this test for the first time against real Postgres: (1) PostgreSQL superusers *always* bypass RLS regardless of `FORCE ROW LEVEL SECURITY` — and both this test's own connecting role and `docker-compose.yml`/CI's `POSTGRES_USER=klaros` are the cluster's initdb bootstrap role, i.e. a superuser, so the original test was asserting nothing about RLS logic and would have failed identically in real CI; (2) Postgres does not guarantee left-to-right evaluation of `AND` conjuncts in a policy's `USING` clause — a residual custom-GUC placeholder (`current_setting` returns `''`, not `NULL`, for a param once set-then-reset on a pooled connection) could reach the `::uuid` cast before the guard clause short-circuited it, depending on planner reordering | Re-run standalone (PASS) and 3x for determinism (PASS all 3), plus inside the full 1428-test suite run (PASS, zero failures for any of the 9 RLS/tenant-context tests) |
| `frontend/package.json` | Added an explicit `"vite": "5.4.21"` devDependency pin | `npx tsc --noEmit` failed (exit 1, 31-line type error) on a fresh `npm ci` because `@vitejs/plugin-react@4.3.4`'s unconstrained `vite` peer range (`^4\|^5\|^6`) let npm hoist `vite@6.4.3` at the top level while `vitest@1.6.1` kept its own nested `vite@5.4.21` — two structurally-incompatible `Plugin`/`PluginOption` types in the same tree. Pinning `vite` explicitly to the version `vitest@1.x` already bundles forces a single deduped version | `npm ls vite` (single deduped `vite@5.4.21` across the tree), `npx tsc --noEmit` (0 errors), `npx vitest run` (21/21, twice) |

Both fixes are minimal, scoped exactly to the defect found, and re-verified
after. Neither touches product/application behavior. `git diff --stat`
confirms the diff footprint stayed small (see §23 equivalent below — git
status).

## 17. Remaining Limitations

- **RLS infrastructure (enabled + policy present) is verified. RLS
  *enforcement* is explicitly NOT enabled** — this remains item 0.3, a future
  phase decision, correctly not started here. Do not read this document as
  "tenant isolation is enforced at the database layer" — it is not, yet;
  the sole enforcement today remains the existing, unenforced, manual
  `.where(tenant_id == ...)` convention.
- **Postgres-availability caveat**: no Docker/Docker Compose exists in this
  sandbox. Real Postgres was obtained via `pgserver` (a PyPI package
  bundling a genuine Postgres 16.2 binary) instead — a real database engine,
  not a mock, not SQLite — but not the exact `ankane/pgvector:v0.5.1` image
  `docker-compose.yml`/`ci.yml` specify. pgvector 0.6.2 (vs. the image's
  0.5.1) was used; no version-specific incompatibility was observed.
- **Docker builds are entirely unverified** (§13) — a real gap, not claimed otherwise.
- **The CI pipeline, as currently configured, would NOT go green on its
  first real GitHub Actions run** — 54 failures + 1 error, none in Phase 0's
  own diff, but all real and all reproducible. This is the single most
  important finding of this verification pass. Recorded as required Phase 1
  (or immediate pre-Phase-1) follow-up work, NOT fixed here (out of this
  task's scope — none of the three root causes touch RLS/tenant-context/
  staging/CI-YAML/autonomy):
  1. Fix `get_embedding_provider()`'s deterministic-provider dimension default (or the `knowledge_chunks` schema/test expectations) so real-Postgres embedding inserts do not fail — affects 33 tests, the largest category.
  2. Update the 3 stale `test_alembic_head_unchanged_this_phase` assertions (currently hardcoded to `"0034"`) to compare against the live `ScriptDirectory` head instead of a hardcoded string.
  3. Fix `OpenAIRealtimeVoiceBridge.open()`'s unconditional `OPENAI_API_KEY`-presence check so its 18 fully-mocked unit tests do not require a real-looking key to be present in the environment.
  4. Investigate the single observed Postgres deadlock (§9) once (1)-(3) are fixed and the suite runs clean — it may not recur once the ~50 preceding rolled-back transactions are eliminated, but this was not re-verified.
- **`backend/.env` credential-hygiene gap** (§2): this is a workstation/process
  hygiene issue (a real API-key-bearing file sitting in a location
  `pydantic-settings` auto-loads), not a Phase 0 code defect — flagged for
  the user's awareness, not fixed in code (there is nothing to fix in the
  Phase 0 diff; the `.env` file itself is correctly gitignored and outside
  version control).
- **Node version**: this session used Node v24.20.0; `ci.yml` pins Node 20.
  No incompatibility was observed, but this was not cross-checked against
  Node 20 specifically.

## 18. Phase 0 Gate

**YES WITH LIMITATIONS**

Phase 0's own scoped deliverables — RLS audit-mode instrumentation, the
tenant-context `SET LOCAL` plumbing, connection-pool-leak safety, concurrency
isolation, the autonomy-deprecation guardrail, the staging secret-hygiene
boot check, and the frontend test harness — are all now genuinely proven
against a real PostgreSQL database, not just reviewed as source. Two real
defects within that scope were found and fixed (§16). Migration 0040
upgrades, downgrades, and re-upgrades cleanly with no drift.

The gate is "WITH LIMITATIONS," not a clean "YES," for two reasons that are
about the *overall CI-readiness claim*, not about the RLS/tenant-context work
itself: (1) Docker/Docker Compose could not be exercised at all in this
sandbox, so the CI pipeline's docker-build jobs remain unverified; (2) this
session is the first time the full backend suite has ever run against real
Postgres, and doing so surfaced 3 categories of genuine, pre-existing,
CI-blocking defects (54 failures + 1 error) in subsystems Phase 0 never
touched (Knowledge/RAG embedding storage, AI Voice Realtime tests, one stale
test assertion file). Phase 0's own work is sound; the repository's overall
"CI will be green" assumption is not yet true and should not be represented
as true until those three items are fixed.
