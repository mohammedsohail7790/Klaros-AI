# PHASE 17B-1 — RESTRICTED APPLICATION DATABASE ROLE — IMPLEMENTATION LOG

Repository: `/Users/mohammedsohail/Desktop/Klaros AI`. Scope: database role separation ONLY — no RLS enforcement, no policy changes, no 100-table instrumentation, no tenant-context propagation changes. Continues directly from `PHASE_17A_RLS_ENFORCEMENT_READINESS_AUDIT.md`.

---

## 1. Starting Git State

```
HEAD: af4937e403e47cdc141f2db349dfcc46a3df6c4b (matches expected)
```

`git status --short` at the start of this phase matched exactly the expected Phase 16a/16b/17A state:

```
 M backend/app/services/business_discovery_service.py
 M backend/app/services/discovery_extraction_service.py
 M backend/tests/test_business_discovery_service.py
?? KLAROS_DISCOVERY_COMPLETION_AUDIT.md
?? PHASE_16A_DISCOVERY_FALLBACK_IMPLEMENTATION_LOG.md
?? PHASE_16B_DISCOVERY_REAL_PROVIDER_VALIDATION_LOG.md
?? PHASE_17A_RLS_ENFORCEMENT_READINESS_AUDIT.md
?? backend/tests/test_discovery_connected_provider_service.py
?? backend/tests/test_discovery_extraction_service.py
```

`git diff --check`: clean. `git diff --stat`: 3 files, 274 insertions(+)/25 deletions(-), matching the expected Discovery-fallback diff exactly. None of this pre-existing state was staged, committed, or modified during this phase — re-confirmed at the end (`git status --short` on those specific paths is byte-identical to the start). No `git add`, `git commit`, or `git push` was run.

---

## 2. Existing Database Role Architecture

Per PHASE_17A §7 (re-confirmed directly in this phase, not just cited): every environment — local Docker Compose, CI, and (per `DEPLOYMENT_RUNBOOK.md`) production — connects to Postgres as the cluster's own bootstrap/initdb role (`POSTGRES_USER`, default `klaros`). PostgreSQL always creates this role as a full superuser (`SUPERUSER`, implicit `BYPASSRLS`, `CREATEDB`, `CREATEROLE`). This same role owns every table (confirmed again in this phase's own real-Postgres run: all 136 `public` tables owned by `klaros`). No second, restricted role existed anywhere in the repository before this phase — no migration, no Docker init script, no CI step, no deployment doc created one.

---

## 3. Target Role Architecture

Two-role model, matching the plan's "owner role owns schema/runs migrations" + "app role runs the application" split:

- **Owner/migration role** — unchanged: the existing bootstrap role (`klaros` by convention). Continues to own every table, run every Alembic migration, and perform administrative DDL. Never used by the application's own runtime traffic once an environment is cut over.
- **Restricted runtime application role** — new, named `klaros_app` by convention (matches and extends the existing `klaros`/`klaros_staging` naming pattern already used in `.env.staging.example`; not a hardcoded requirement — every provisioning entry point takes the name as config). `LOGIN`, `NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`, `NOBYPASSRLS`, `NOREPLICATION`, never a table owner. Used by FastAPI, the Temporal worker, and the Event Worker for all normal DML traffic.

---

## 4. Role/Privilege Design

Determined mechanically (not guessed) by inspecting the schema and ORM base class:

- `backend/app/db/base.py` generates every primary key client-side (`default=uuid.uuid4`, `Uuid` columns) — no `Sequence`/`Identity`/integer-autoincrement primary keys exist anywhere in `app/models/*.py` (confirmed via grep for `Integer, primary_key=True` / `BigInteger, primary_key=True` / `Sequence(` / `Identity(` — no matches). Sequence privileges are therefore not load-bearing for INSERTs today, but are still granted defensively (`USAGE, SELECT, UPDATE ON ALL SEQUENCES`) since nothing in the schema forbids a future migration from introducing one.
- The `vector` extension (migration `0032`, `pgvector`) is the only extension in use; its operators/functions live in `public` and are covered by an explicit `GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public` plus the matching `ALTER DEFAULT PRIVILEGES` clause.
- Granted, explicitly, never via a blanket "ALL PRIVILEGES":
  - `CONNECT` on the database
  - `USAGE` on schema `public`
  - `SELECT, INSERT, UPDATE, DELETE` on every table in `public` (existing tables, via `GRANT ... ON ALL TABLES`, plus `ALTER DEFAULT PRIVILEGES FOR ROLE <owner> ... GRANT ... ON TABLES` so every table a *future* Alembic migration creates — still run as the owner — is automatically covered with no re-grant step)
  - `USAGE, SELECT, UPDATE` on every sequence (+ the matching default-privilege clause)
  - `EXECUTE` on every function (+ the matching default-privilege clause)
- Explicitly NOT granted: `SUPERUSER`, `BYPASSRLS`, `CREATEDB`, `CREATEROLE`, `REPLICATION`, table/schema ownership, `CREATE` on the schema (confirmed by the negative test in §12 — the role cannot `CREATE TABLE`).

Implementation: `backend/scripts/db/provision_app_role.py` — an idempotent, asyncpg-based Python script that connects as the OWNER role (never the role it is creating) and runs exactly the statements above. Idempotent by design: `CREATE ROLE` is guarded by an `pg_roles` existence check (falls through to `ALTER ROLE` to keep the password/flags authoritative on rerun); every `GRANT`/`ALTER DEFAULT PRIVILEGES` statement is naturally idempotent in PostgreSQL. Safe to run before OR after `alembic upgrade head`. The password is dollar-quoted with a randomly generated, collision-checked delimiter (PostgreSQL's `CREATE`/`ALTER ROLE ... PASSWORD` clause requires a literal token in the grammar, not a bind parameter) — never logged, never printed, never written to any file.

A second, thin implementation of the same statements exists for the one place a Python script cannot run: `backend/scripts/db/docker-initdb/01-create-app-role.sh`, a plain-SQL `psql` script for Postgres's own `/docker-entrypoint-initdb.d/` convention (that mechanism runs inside the `postgres` container itself, which has no Python/backend code available — only `psql`). Both are documented as needing to stay in sync; they are small and mechanically identical.

---

## 5. Ownership Design

No object's ownership was transferred. Inventory taken directly against a real, fully-migrated Postgres instance (§10 below):

```
SELECT tableowner, count(*) FROM pg_tables WHERE schemaname='public' GROUP BY tableowner;
 tableowner | count
------------+-------
 klaros     |   136
```

All 136 tables remain owned by the owner role. `klaros_app` owns zero tables (`SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tableowner='klaros_app'` → `0`), verified both immediately after provisioning and after running representative DML through the app role. This satisfies the plan's target ("application role != object owner") without any `REASSIGN OWNED` or `ALTER TABLE ... OWNER TO` — the owner role keeps running every migration exactly as before, and the app role only ever receives explicit `GRANT`s.

---

## 6. Environment Configuration

New settings field: `backend/app/core/config.py::Settings.DATABASE_MIGRATION_URL` (`str | None = None`). When unset, every owner/migration operation falls back to `DATABASE_URL` — i.e. today's existing single-role behavior, completely unchanged, for any environment that has not been cut over yet (dev machines with an old `.env`, CI as it stands today, any not-yet-updated staging/production). `backend/alembic/env.py` now resolves `DATABASE_MIGRATION_URL or DATABASE_URL` for its connection, so Alembic always runs privileged and the application never silently falls back to it.

`docker-compose.yml`: `postgres` service gains `APP_DB_USER`/`APP_DB_PASSWORD` (defaults `klaros_app`/`klaros_app`, matching the existing dev-default pattern for `POSTGRES_USER`/`PASSWORD`) and a read-only bind mount of `backend/scripts/db/docker-initdb` into `/docker-entrypoint-initdb.d`. `backend`, `worker`, and `event-worker` services now set `DATABASE_URL` to the `APP_DB_USER` connection and add `DATABASE_MIGRATION_URL` set to the existing `POSTGRES_USER` connection.

`docker-compose.prod.yml`: adds the same "no insecure fallback" guard already used for `POSTGRES_PASSWORD` — `APP_DB_PASSWORD` is now `${APP_DB_PASSWORD:?...must be set explicitly...}`, refusing to boot with the dev default in a real deployment.

`.env.example` / `.env.staging.example`: documented with the same two-URL pattern and explanatory comments; staging's example additionally documents that `provision_app_role.py` must be run once (against `DATABASE_MIGRATION_URL`) before that environment's first real deploy.

---

## 7. Files Changed

- `backend/app/core/config.py` — added `DATABASE_MIGRATION_URL` setting.
- `backend/alembic/env.py` — resolves `DATABASE_MIGRATION_URL or DATABASE_URL`.
- `backend/scripts/db/provision_app_role.py` — new; idempotent role provisioning (Python/asyncpg).
- `backend/scripts/db/docker-initdb/01-create-app-role.sh` — new; equivalent provisioning for Postgres's own initdb hook (local dev, fresh volumes only).
- `docker-compose.yml` — `postgres` service env + init-script mount; `backend`/`worker`/`event-worker` services' `DATABASE_URL`/`DATABASE_MIGRATION_URL`.
- `docker-compose.prod.yml` — required, no-fallback `APP_DB_PASSWORD`.
- `.env.example`, `.env.staging.example` — documented the new variables.
- `.github/workflows/ci.yml` — added a step that provisions the role against CI's own Postgres service and asserts its `pg_roles`/`pg_tables` attributes (does not repoint the main pytest run's `DATABASE_URL` — see §9/§19 for why).
- `backend/tests/test_restricted_app_role_cutover.py` — new; 9 real-Postgres tests (see §9/§13/§16).
- `backend/tests/test_migration_schema_matches_models.py` — one-line-equivalent fix: this test's subprocess now also pins `DATABASE_MIGRATION_URL` to its own disposable SQLite file, so it is not affected by a `DATABASE_MIGRATION_URL` already present in the parent process's environment (see §14, item 2, for the exact failure this fixes and why it is a real, legitimate interaction with this phase's own change, not a false positive).

No other file was touched. `backend/app/services/business_discovery_service.py`, `discovery_extraction_service.py`, `test_business_discovery_service.py`, and the untracked Discovery/Phase-16 files remain exactly as they were at the start of this phase (see §1).

---

## 8. Database Objects Changed

Per real-Postgres verification run (§10): one new role (`klaros_app`-named role in that run), zero new/altered tables, zero new/altered RLS policies, zero ownership changes. `GRANT`/`ALTER DEFAULT PRIVILEGES` statements are the only DDL-adjacent objects touched, and they are additive/non-destructive (never a `REVOKE` from the owner role, never a `DROP`).

---

## 9. Migration/Provisioning Strategy

Role creation deliberately does NOT live in an Alembic migration: Alembic migrations are version-controlled, environment-agnostic SQL, and a role's password is environment-specific secret material that must never be hardcoded or committed — putting `CREATE ROLE ... PASSWORD 'x'` in a migration file would either hardcode a real password in git history or require Alembic to somehow source a secret mid-migration, neither of which fits this codebase's existing secret-handling convention (env vars only, `.env`/deployment-secret-manager, never in source).

Instead: `backend/scripts/db/provision_app_role.py` is a standalone, idempotent, out-of-band script, run once per environment (and safely re-runnable) with `DATABASE_MIGRATION_URL`/`APP_DB_USER`/`APP_DB_PASSWORD` supplied via the environment, exactly like Alembic itself is already invoked out-of-band per `DEPLOYMENT_RUNBOOK.md`. Local Docker Compose additionally gets it "for free" on a fresh volume via the `docker-entrypoint-initdb.d` hook (§4), since that mechanism has access to container-level env vars but not Python.

Staging/production: not executable from this sandbox (no network access to a real staging/production Postgres). Documented in `.env.staging.example` and in §19 below as the exact command an operator must run once (`DATABASE_MIGRATION_URL=... APP_DB_USER=... APP_DB_PASSWORD=... python -m scripts.db.provision_app_role`) before cutting `DATABASE_URL` over in that environment's own deployment config.

---

## 10. Real PostgreSQL Verification

Per task instruction (Docker unavailable in this sandbox), used the `pgserver` PyPI package already vendored in `backend/.venv` — a real, disposable PostgreSQL 16-family instance, not SQLite, not a mock.

Procedure actually run, in order:
1. Started a disposable instance rooted outside the repository (`/tmp/phase17b1_pgdata`, destroyed at the end of this phase).
2. `CREATE ROLE klaros WITH LOGIN SUPERUSER PASSWORD '<redacted>'; CREATE DATABASE klaros OWNER klaros;` — mirrors the real deployment's actual bootstrap-role reality (§2).
3. `alembic upgrade head` against that database, connected as `klaros` — all 51 migrations applied cleanly, exit code 0 (same result PHASE_17A's own audit already established; re-confirmed here as a live prerequisite for the steps below, not re-litigated as new evidence about migration correctness itself).
4. `DATABASE_MIGRATION_URL=<klaros dsn> APP_DB_USER=klaros_app APP_DB_PASSWORD=<redacted> python -m scripts.db.provision_app_role` — succeeded.
5. Direct `pg_roles`/`pg_tables` catalog queries (as `klaros`):

```
SELECT rolname, rolsuper, rolbypassrls, rolcanlogin, rolcreatedb, rolcreaterole, rolreplication
FROM pg_roles WHERE rolname IN ('klaros','klaros_app');

  rolname   | rolsuper | rolbypassrls | rolcanlogin | rolcreatedb | rolcreaterole | rolreplication
------------+----------+--------------+-------------+-------------+---------------+----------------
 klaros     | t        | f            | t           | f           | f             | f
 klaros_app | f        | f            | t           | f           | f             | f

SELECT tableowner, count(*) FROM pg_tables WHERE schemaname='public' GROUP BY tableowner;
 tableowner | count
------------+-------
 klaros     |   136

SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tableowner='klaros_app';
 count
-------
     0
```

Exactly matches the success criteria in §24 of the task: `rolsuper=false`, `rolbypassrls=false`, `rolcanlogin=true`, zero tables owned by the app role.

6. Connected AS `klaros_app` directly (psql) and ran: `SELECT current_user, session_user` (both `klaros_app`); `SELECT count(*) FROM company_memories` (a Tier-1 RLS-audit-mode table, `0`, no error); `SELECT count(*) FROM organizations` (an un-instrumented, no-RLS table, `0`, no error); a full `INSERT`/`UPDATE`/`DELETE` cycle on `company_memories` (all succeeded). See §12 for the negative-test half of this same session.

The instance was stopped and its data directory deleted at the end of this phase; nothing was left running against the repository or reachable from outside this session.

---

## 11. Runtime Verification

Full FastAPI application boot/HTTP-level "start the app as the restricted role" was not exercised via `uvicorn` directly in this pass (no docker daemon in this sandbox — see PHASE_17A's own note on the same constraint). What WAS verified, which is the load-bearing claim for this phase's own scope (application DML, not the full HTTP stack): direct `asyncpg`/SQLAlchemy-engine DML as the restricted role, through the exact same code paths the application's own `AsyncSession`/`async_session_maker` use (`backend/tests/test_restricted_app_role_cutover.py` builds a real `create_async_engine` against the app role and runs `INSERT`/`SELECT`/`UPDATE`/`DELETE` through it, plus the pool-safety `set_tenant_context` flow — see §13). Full HTTP-endpoint-level regression (register/login/Discovery/Blueprint/etc.) was verified via the existing backend test suite (§14) running against real Postgres with the owner role unchanged (the suite's own schema-management fixtures require owner-level DDL — see §16's explanation of why the *whole* suite is not repointed at the app role) — this proves the application code itself is unaffected by this phase's changes, while the dedicated role-cutover test file proves the *restricted role itself* is fully capable of the DML the application issues.

---

## 12. Security Verification

Real-Postgres negative tests, run directly as `klaros_app` (psql session, same instance as §10) and independently re-proven via `backend/tests/test_restricted_app_role_cutover.py`'s `test_app_role_cannot_create_alter_or_drop_tables` / `test_app_role_cannot_create_roles_or_elevate_itself`:

```
CREATE TABLE should_fail(id int);                          -> ERROR: permission denied for schema public
ALTER TABLE company_memories ADD COLUMN should_fail int;    -> ERROR: must be owner of table company_memories
DROP TABLE company_memories;                                -> ERROR: must be owner of table company_memories
CREATE ROLE should_fail LOGIN;                               -> ERROR: permission denied to create role
ALTER ROLE klaros_app SUPERUSER;                              -> ERROR: permission denied to alter role
```

`SELECT current_user, session_user` as the app role both report `klaros_app` — no silent identity fallback. The role cannot elevate itself, create other roles, or perform schema DDL, while ordinary DML (§10, step 6) succeeds without error — exactly the intended shape.

---

## 13. Pool/Connection Verification

`backend/tests/test_restricted_app_role_cutover.py::test_app_role_pooled_tenant_context_does_not_leak` re-runs the exact same assertions as `backend/tests/test_postgres_rls_audit_mode.py::test_set_local_tenant_context_does_not_leak_across_pooled_connections` (tenant A → tenant B → tenant A across repeated acquire/release cycles, asserting `app.tenant_id` is unset at the start of every fresh session), but through a pooled `async_sessionmaker` built on the restricted role's own engine instead of the owner engine. Passed. The original test (owner role, unmodified) was also re-run in this phase and still passes (§14) — confirming the role cutover does not alter this already-proven-safe connection/session behavior for either role.

---

## 14. Backend Test Results

Baseline re-derived from Phase 16b's own regression numbers per the task's instruction (Phase 16b's own log records the same shape: ~1888 collected, 1 pre-existing unrelated voice-websocket failure, 12 skipped — consistent with the task prompt's stated historical range of 1845–1879 passed depending on which Discovery tests are counted).

Two full real-Postgres runs were executed in this phase (`python -m pytest -q`, `DATABASE_URL`/`DATABASE_MIGRATION_URL` both pointed at the same disposable instance as §10, `EVENT_TRANSPORT=memory`, `AI_PROVIDER=deterministic`, `EMBEDDING_PROVIDER=deterministic`, `STT_PROVIDER=deterministic`, `TTS_PROVIDER=deterministic`, matching CI's own env — connection still as the OWNER role, per §16's explanation):

- **Run 1** (before a fix described below): `2 failed, 1887 passed, 12 skipped`.
  - `tests/test_openai_realtime_voice_service.py::test_voice_stream_route_dispatches_to_realtime_engine_when_configured` — re-ran in isolation and it passed; this is the pre-existing, unrelated, order-dependent voice-websocket flake the task's own baseline already names, not something this phase introduced.
  - `tests/test_migration_schema_matches_models.py::test_migration_chain_produces_columns_the_orm_models_declare` — a REAL interaction with this phase's own `alembic/env.py` change: this test spawns `alembic upgrade head` as a subprocess with `DATABASE_URL` overridden to a disposable SQLite file, but (before the fix) did not also override `DATABASE_MIGRATION_URL` — and my own test/verification shell session had `DATABASE_MIGRATION_URL` exported (pointing at the real Postgres instance) for the *parent* pytest process, which leaked into the subprocess's inherited environment. Since `alembic/env.py` now prefers `DATABASE_MIGRATION_URL` over `DATABASE_URL` when set, the subprocess silently migrated the real Postgres database (already at `head`, so a no-op) instead of the intended fresh SQLite file, which was therefore never migrated — the test then read an empty column set from that untouched file and failed. **Root cause classification: APPLICATION ROLE ASSUMPTION / TEST FIXTURE ISSUE** (a test that overrides `DATABASE_URL` alone now needs to also override `DATABASE_MIGRATION_URL`, since the two are no longer guaranteed identical). **Fix**: `backend/tests/test_migration_schema_matches_models.py` now also pins `DATABASE_MIGRATION_URL` to the same disposable SQLite path in the subprocess env, with a comment explaining exactly this interaction — not a workaround, the correct fix given the new two-URL model. This is a legitimate, narrowly-scoped, in-scope test fix (§21 explicitly allows "tests specifically validating the role cutover" and this is a direct, correct consequence of introducing `DATABASE_MIGRATION_URL`), not a product-code change.
- **Run 2** (after the fix, full clean re-run): **`1 failed, 1888 passed, 12 skipped`** — the 1 failure is the same pre-existing voice-websocket test, confirmed independently pre-existing (passes in isolation; a known async/event-loop test-ordering flake unrelated to database roles). This matches the re-derived Phase 16b baseline shape exactly (1 pre-existing failure, 12 skipped) with one additional passing test (1888 vs the prior count) — `backend/tests/test_restricted_app_role_cutover.py`'s 9 new tests plus other tests already counted in the baseline window.

`backend/tests/test_postgres_rls_audit_mode.py` (all 6 tests) and `backend/tests/test_restricted_app_role_cutover.py` (all 9 tests) were also each re-run standalone for direct confirmation: **6 passed** / **9 passed** respectively, both against the same real-Postgres instance.

No test was skipped, deleted, or weakened to make this pass. `test_postgres_rls_audit_mode.py` is untouched (per the explicit instruction in §15 of the task) — its probe-role technique remains available for future enforcement-phase tests and is conceptually distinct from this phase's own `klaros_app` runtime role (see §17 below).

---

## 15. Frontend Regression Results

No frontend source file was changed in this phase. Ran, against the actual repo (real Vitest/tsc/Next.js, not simulated):

- `npx tsc --noEmit` — clean, zero errors.
- `npx vitest run` — **15 test files, 89 tests, all passed** (one pre-existing, unrelated `act(...)` warning from `app/business/recommendations/page.tsx`'s own async state update, not a failure, not touched by this phase).
- `npm run build` (`next build`) — completed successfully, full route manifest generated, no errors.

---

## 16. Secret Scan

`git diff` across every file this phase touched was manually reviewed for credential-shaped strings. No real password, API key, or token appears anywhere in the diff. The only password-shaped literals present are the same pre-existing, well-known, non-secret local-dev/CI defaults already committed before this phase (`klaros`/`klaros_app`, matching the existing `POSTGRES_PASSWORD=klaros` convention already in `docker-compose.yml`/`.env.example`/`ci.yml`), plus explicit placeholders (`CHANGE_ME`, `<generate-a-real-random-staging-only-secret>`) that were already this repository's established convention for `.env.staging.example` before this phase and are extended identically for the new `APP_DB_PASSWORD`/`DATABASE_MIGRATION_URL` entries. The disposable Postgres passwords generated and used during this phase's own real-Postgres verification (§10–§13) existed only inside a now-deleted `/tmp` data directory and this session's own shell environment; none were written to any file under the repository, none appear in this report, and none are reused anywhere.

---

## 17. Known Limitations

- The `docker-entrypoint-initdb.d` mechanism only runs against a brand-new `postgres_data` Docker volume. Any developer with an existing local volume from before this phase will not get `klaros_app` automatically — they must run `provision_app_role.py` manually once (documented in `docker-compose.yml`'s own comment). This was not exercised against a real Docker daemon in this sandbox (none available — same constraint PHASE_17A and prior Docker-related work already documented); the shell-script logic itself mirrors the Python script's statements exactly and was not independently executed against a live `docker-entrypoint-initdb.d` run.
- CI's main `pytest -q` step still connects as the owner role (see §19/§21 for why — most of the suite's real-Postgres tests manage their own schema via `Base.metadata.create_all`, an inherently owner-level operation). CI's new step provisions and directly verifies the restricted role's `pg_roles`/`pg_tables` attributes, but does not run the full application test suite through it. `backend/tests/test_restricted_app_role_cutover.py` is what actually proves DML-level correctness for the restricted role, using its own throwaway role/engine rather than the suite-wide `DATABASE_URL`.
- Staging and production role cutover could not be executed or verified from this sandbox (no network reachability to any real staging/production Postgres instance). `.env.staging.example` and §19 document the exact steps and required env vars; actually running them against a real staging database is a REQUIRED, NOT-YET-DONE follow-up (see §18/§19), not something this phase could complete.
- The `DATABASE_MIGRATION_URL`-leaks-into-a-subprocess interaction found and fixed in `test_migration_schema_matches_models.py` (§14) is a general shape of risk worth remembering: any future script/test that overrides only `DATABASE_URL` and expects Alembic or `provision_app_role.py` to follow it must also override `DATABASE_MIGRATION_URL` if the ambient environment might have it set. No other such call site was found in this pass (grepped for `subprocess` + `alembic` across `backend/`), but a full audit of every script that shells out to `alembic` was not exhaustively re-run beyond this one file.

---

## 18. Remaining Phase 17B Blockers

Per PHASE_17A §23's ordered plan, this phase (17B-1) only completes step 1 (restricted role). Still blocking full RLS enforcement, unchanged from PHASE_17A's own findings and explicitly NOT touched in this phase:

- 17B-2: close the tenant-context propagation gaps (Event Bus, MCP, public website, webhooks — PHASE_17A §5/§16/§17).
- 17B-3: design the system/global-context model for legitimate cross-tenant operations (PHASE_17A §13).
- 17B-4: instrument the 100 currently-unprotected tenant tables in audit mode (PHASE_17A §4).
- 17B-5/17B-6: write real enforcing policies and flip `FORCE ROW LEVEL SECURITY`, staged per-domain (PHASE_17A §20/§21).
- Actually executing this phase's own role cutover against real staging/production Postgres (this sandbox has no reachability to either — see §17).

---

## 19. Exact Next Step

Environment matrix (Environment | Owner role | Runtime role | RLS enforced?):

| Environment | Owner role | Runtime role | RLS enforced? |
|---|---|---|---|
| Local Docker Compose | `klaros` (`POSTGRES_USER`) | `klaros_app` (`APP_DB_USER`) — structurally cut over in `docker-compose.yml`; provisioned automatically on a fresh volume via `docker-entrypoint-initdb.d`, or manually via `provision_app_role.py` on an existing one | No (audit-mode only, unchanged) |
| CI (`.github/workflows/ci.yml`) | `klaros` | `klaros_app` is provisioned and its `pg_roles`/`pg_tables` attributes are directly verified by a dedicated step; the main `pytest -q` run still connects as `klaros` (see §17) | No (audit-mode only, unchanged) |
| Staging | Not yet provisioned — **BLOCKED**, no reachability from this sandbox | N/A yet | No |
| Production | Not yet provisioned — **BLOCKED**, no reachability from this sandbox | N/A yet | No |

Exact next step for staging/production (an operator with real access must run this — not executable from this sandbox): pick real, distinct role names/passwords for that environment (never the local-dev defaults), set them as `APP_DB_USER`/`APP_DB_PASSWORD` in that environment's secret manager, set `DATABASE_MIGRATION_URL` to the existing owner connection string, run `DATABASE_MIGRATION_URL=... APP_DB_USER=... APP_DB_PASSWORD=... python -m scripts.db.provision_app_role` once from a machine with real network access to that Postgres instance, verify with the same `pg_roles`/`pg_tables` queries used in §10, then and only then repoint that environment's own `DATABASE_URL` (the one the running application actually uses) at the new restricted role and redeploy. Keep the owner-role `DATABASE_URL` available separately for `alembic upgrade head` and for rollback (repoint the application back to the owner role if anything goes wrong — the restricted role is strictly additive, nothing about the owner role's own access was changed or revoked).

Once every environment in the matrix above shows a real, cut-over runtime role, Phase 17B-2 (tenant-context propagation) becomes unblocked.

---

## 20. Final Verdict

See the response below.
