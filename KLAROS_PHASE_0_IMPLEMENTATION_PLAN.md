# Klaros AI — Phase 0 Implementation Plan

Covers item W. Phase 0 scope derived from the actual gaps confirmed in this review (KLAROS_ARCHITECTURE_REVIEW.md §4), not assumed wholesale from the source documents — cross-checked against KLAROS_GAP_ANALYSIS.md's five P0 items and found to match: autonomy-model correction, RLS foundation, agent-loop/depth protection groundwork, no staging environment, no CI. Every item below follows WHY NOW / CURRENT STATE / TARGET STATE / FILES-SUBSYSTEMS AFFECTED / DEPENDENCIES / MIGRATION / TESTS / RISKS / ROLLBACK / DONE WHEN.

## 0.1 — CI pipeline (lint + typecheck + existing test suite)

- **Why now:** every other Phase 0 item (RLS, security gate, migration safety) is only as trustworthy as a pipeline that actually runs its tests on every PR. Confirmed zero CI exists — this is the first thing that makes every subsequent claim in this roadmap verifiable rather than aspirational.
- **Current state:** no `.github/`, no CI config, `requirements-dev.txt`'s pip-audit comment is stale/aspirational.
- **Target state:** GitHub Actions (or equivalent) pipeline running: backend lint (confirm/add config), backend typecheck (decide tool), `pytest` (existing 183 files), frontend `next lint`, frontend `tsc --noEmit`, `pip-audit`, `npm audit`, Docker build for both images, Alembic migration-validation stage (spin up throwaway Postgres, run all 39+ migrations head-to-head).
- **Files/subsystems affected:** new `.github/workflows/ci.yml` (or platform equivalent); no application code changes.
- **Dependencies:** none — this can start immediately, in parallel with everything else.
- **Migration:** none.
- **Tests:** the pipeline's own correctness is validated by intentionally breaking a test locally and confirming the pipeline catches it before merge.
- **Risks:** existing backend test suite may reveal currently-unknown failures once actually run in CI (183 files' pass/fail status was not verified in this review) — treat any failures found as Phase 0 work items, not blockers to standing up CI itself (stand up CI first, allowing known-failing tests to be quarantined with a tracked ticket, rather than blocking CI on fixing everything first).
- **Rollback:** disable the workflow file; zero application impact.
- **Done when:** every PR against `main` runs all stages above and merge is blocked on failure (except explicitly quarantined pre-existing failures).

## 0.2 — RLS instrumentation (permissive/audit mode, zero behavior change)

- **Why now:** tenant isolation is CRITICAL-classified (KLAROS_FINAL_SECURITY_MODEL.md) and is app-layer-only today; every subsequent phase adds more tenant-scoped tables, so the plumbing must exist before the pile grows further.
- **Current state:** `TenantScopedMixin` is column-only; `get_tenant_db()` returns an unscoped session; isolation is a manual `.where()` convention, verified by sampling.
- **Target state:** `SET LOCAL app.tenant_id` wired into the request-scoped `get_db()` dependency and into the Temporal worker/event-worker's per-activity DB access; RLS policies added in permissive/audit mode (evaluates true, but paired with a shadow-check that logs any row that would have failed a real policy) on the 5 highest-sensitivity tables: `IntegrationConnection`, `ApprovalRequest`, `AuditLog`, `CompanyMemory`, `Organization`/`User`.
- **Files/subsystems affected:** `backend/app/db/session.py`, `backend/app/api/deps.py` (`get_db`/`get_tenant_db`), `backend/app/workers/main.py`, `backend/app/events/worker` entrypoint, new Alembic migrations for the 5 tables' policies.
- **Dependencies:** 0.1 (CI) should exist first so this change is tested on every subsequent PR, not just at merge time.
- **Migration:** 5 additive migrations (policy only, no schema change to the tables themselves).
- **Tests:** new tenant-isolation test suite (KLAROS_FINAL_TESTING_ARCHITECTURE.md) exercised in audit mode — confirms zero behavior change while collecting evidence of any query path that would have failed a real policy.
- **Risks:** a `SET` (non-`LOCAL`) mistake could leak tenant context across pooled-connection reuse — the single highest-risk implementation detail in this entire plan; mitigated by a dedicated connection-pool-reuse test before this item is marked done.
- **Rollback:** drop the 5 policies; `SET LOCAL` plumbing is harmless to leave in place (a no-op without a corresponding policy).
- **Done when:** audit-mode logging runs in staging for at least one full week with zero unexpected shadow-check failures, and the connection-pool-reuse test passes.

## 0.3 — RLS enforcement, permissive-with-bypass (same 5 tables, then expand)

- **Why now:** instrumentation alone doesn't protect anything; this is the step that actually closes the CRITICAL gap.
- **Current/target state:** as in KLAROS_FINAL_SECURITY_MODEL.md §D steps 2-3.
- **Files/subsystems affected:** same as 0.2, plus a new `app_migrator` bypass role.
- **Dependencies:** 0.2 must have run clean in staging first.
- **Migration:** alters the 5 policies from audit-mode to `FORCE ROW LEVEL SECURITY` enforcing.
- **Tests:** the mandatory tenant-isolation cross-tenant-read/write suite, now asserting actual denial, not just logging.
- **Risks:** any legitimate cross-tenant admin/support query path not yet identified will break — mitigated by the bypass role being available for genuine admin use, audited.
- **Rollback:** revert to permissive/audit mode per-table (KLAROS_FINAL_DATABASE_ARCHITECTURE.md rollback strategy).
- **Done when:** the 5 tables are RLS-enforced in production with the mandatory test suite green in CI; expansion to the remaining ~185 tenant-scoped tables continues as an ongoing Phase 0/1 workstream, by sensitivity, not blocking the rest of Phase 0.

## 0.4 — Deprecate `Organization.autonomy_level` as an enforcement concept

- **Why now:** closes the specific, three-times-independently-re-verified dead-field finding before any Agent work (Phase 7) could accidentally build on top of it or a new engineer could mistake it for functional.
- **Current/target state:** KLAROS_ARCHITECTURE_REVIEW.md §5, KLAROS_FINAL_SECURITY_MODEL.md §B.
- **Files/subsystems affected:** `backend/app/models/organization.py` (comment update only, marking it explicitly deprecated in docstring, no code removal), a CI grep-check added to 0.1's pipeline that fails a PR introducing a new read of `autonomy_level` in application logic.
- **Dependencies:** 0.1 (CI, to host the grep-check).
- **Migration:** none.
- **Tests:** the grep-check itself is the test.
- **Risks:** none — this is a documentation/guardrail change with zero behavioral impact, by design.
- **Rollback:** not applicable (no behavior changed).
- **Done when:** the CI grep-check is live and the model's docstring reflects deprecated status.

## 0.5 — Staging environment

- **Why now:** the pre-autonomy security gate (KLAROS_FINAL_SECURITY_MODEL.md) requires "at least one staging dry run" before any agent-tier feature ships — there is currently nowhere to run one. Also required to validate `docker-compose.prod.yml`, which is self-documented as never exercised against a real Docker daemon.
- **Current state:** no staging environment; no deployment manifests of any kind.
- **Target state:** a staging deploy mirroring the target production topology (per KLAROS_FINAL_DEPLOYMENT_ARCHITECTURE.md), reachable by the new CI pipeline's "Deploy staging" stage, running the actual `docker-compose.prod.yml` overlay for the first time.
- **Files/subsystems affected:** new deployment manifests (platform-choice-dependent, **UNKNOWN — REQUIRES DECISION** on managed-PaaS vs. self-hosted, out of scope to decide here).
- **Dependencies:** 0.1 (CI, to deploy from).
- **Migration:** none to application, but this is where migration-validation (0.1's stage 8) gets its first real-topology exercise.
- **Tests:** smoke tests against `/health`/`/ready` and a handful of critical paths.
- **Risks:** `docker-compose.prod.yml` may reveal issues on first real exercise (it's explicitly untested) — expected, and exactly why this item exists in Phase 0 rather than being assumed to work.
- **Rollback:** staging is disposable by definition; no production risk.
- **Done when:** a full deploy-to-staging-and-smoke-test cycle runs green from the CI pipeline at least once.

## 0.6 — Loop/depth protection groundwork (schema only, no autonomous execution yet)

- **Why now:** identified as a P0 gap in KLAROS_GAP_ANALYSIS.md; the schema fields need to exist before Phase 7's Agent Runtime ships so they aren't bolted on under time pressure later.
- **Current/target state:** no `AgentVersion`/`AgentExecution` tables exist yet (they ship in Phase 7 per KLAROS_FINAL_DATABASE_ARCHITECTURE.md's sequencing) — this item is scoped narrowly to designing and documenting the `max_tool_chain_depth`/`max_executions_per_hour`/`max_concurrent_executions` fields and the enforcement check's placement in the governance chain (KLAROS_FINAL_AGENT_MODEL.md §Governance chain step 9), so Phase 7 implements against a settled design rather than an open question.
- **Files/subsystems affected:** design documentation only in Phase 0 (this document set); actual table/enforcement code lands in Phase 7.
- **Dependencies:** none for the design; Phase 7 depends on this design being settled.
- **Migration:** none in Phase 0.
- **Tests:** none in Phase 0 (design-only item).
- **Risks:** none.
- **Rollback:** not applicable.
- **Done when:** the design is reflected consistently across KLAROS_FINAL_AGENT_MODEL.md and KLAROS_FINAL_TESTING_ARCHITECTURE.md (already true as of this document set).

## 0.7 — Frontend test harness stand-up (empty but wired)

- **Why now:** confirmed zero frontend tests exist; per KLAROS_FINAL_TESTING_ARCHITECTURE.md this must not be deferred to "whenever the first new feature lands."
- **Current/target state:** Vitest + RTL + Playwright configured, wired into CI (0.1), with a minimal smoke test (e.g. renders `AppShell` without crashing) as the first real test — not left at zero tests, but not required to have meaningful coverage yet.
- **Files/subsystems affected:** new `frontend/vitest.config.ts`, `frontend/playwright.config.ts`, `frontend/package.json` script additions.
- **Dependencies:** 0.1 (CI).
- **Migration:** none.
- **Tests:** the harness's own smoke test is the acceptance test.
- **Risks:** none — purely additive tooling.
- **Rollback:** remove config files; zero application impact.
- **Done when:** `npm run test` and `npm run test:e2e` both run in CI and pass on the smoke test.

## Phase 0 exit criteria (aggregate)

All seven items above DONE. No user-facing feature work (Discovery, Blueprint, Agents, Website Builder) begins until this phase's CI, RLS-instrumentation-through-first-5-tables, and staging-environment items are complete — this is the literal meaning of "Agent Runtime hard-gated behind Phase 0" carried through from the source roadmap and reaffirmed here.
