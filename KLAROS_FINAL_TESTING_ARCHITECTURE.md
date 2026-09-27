# Klaros AI — Final Testing Strategy

Covers item T. Verified current state: backend `pytest.ini` real (asyncio_mode=auto, session-scoped event loop), `backend/tests/` contains 183 test files (existence/collection confirmed; pass/fail status of the full suite was not re-run in this review — flagged **UNKNOWN — REQUIRES VERIFICATION**, do not assume green). Frontend: zero test framework, zero test files, confirmed via exhaustive check of `frontend/package.json` and the whole `frontend/` tree.

## Principle: preserve and expand, never discard

All 183 existing backend test files remain as-is. New test layers below are additive. No existing test is removed or rewritten as part of this plan; any test found to be actually failing during Phase 0's CI-standup (see below) is fixed or explicitly quarantined with a tracked reason, never silently deleted.

## Frontend test framework decision (closes an open item from the source documents)

**Vitest + React Testing Library + Playwright** — decided now, not left open. Reasoning: Vitest integrates natively with the existing Vite-less-but-ESM Next.js toolchain with minimal config, RTL matches the existing component structure (`components/ui/*` hand-rolled, no component library to fight), Playwright covers the multi-page flows this whole roadmap adds (Discovery→Blueprint→Recommendations→Website is inherently a multi-page E2E flow). This must ship in **Phase 0**, not be deferred to whenever the first new frontend feature lands — an empty test harness with CI wired to run it is itself the Phase 0 deliverable; meaningful coverage grows feature-by-feature afterward.

## Test-layer matrix

| Layer | Scope | New or existing | CI gate? |
|---|---|---|---|
| Unit (backend) | Existing 183 files + new per-subsystem unit tests as each new service ships | Existing, expanded | Yes, every PR |
| Unit (frontend) | New — component-level (`StatCard`, `Toast`, form validation logic) | New | Yes, every PR (from Phase 0 onward) |
| Integration (API) | Existing router-level tests + new tests per new endpoint group (KLAROS_FINAL_API_ARCHITECTURE.md) | Existing, expanded | Yes |
| Database/migration | New: spin-up-throwaway-DB, run all Alembic migrations head-to-head | New | Yes (CI stage 8, KLAROS_FINAL_DEPLOYMENT_ARCHITECTURE.md) |
| **Tenant isolation (RLS)** | New: for every RLS-enabled table, attempt cross-tenant read/write as Tenant A against Tenant B's row, assert zero rows/permission denied | New | **Yes, mandatory, non-skippable for any PR touching a table on or being added to RLS** |
| Agent-runtime | New: governance-chain order tests (each of the 12 steps in KLAROS_FINAL_AGENT_MODEL.md fires in order, each can independently block), `AgentToolPermission` allowlist enforcement, autonomy-tier ceiling enforcement | New | Yes, before any agent ships above Observe tier |
| Tool-governance | Existing `ToolRegistry` pipeline tests + new tests confirming the 2 new agent-checks don't alter existing 9-step behavior for non-agent actors | Existing, expanded | Yes |
| Approval workflows | Existing `ApprovalRequest` tests + new tests for `PublishRequest` (website) and agent-triggered approvals | Existing, expanded | Yes |
| Workflow execution | Existing Temporal workflow tests + new tests for `AgentExecutionWorkflow`/`BusinessLaunchWorkflow` + existing deterministic Automation Engine tests | Existing, expanded | Yes |
| Integration adapters | Existing per-adapter tests (Stripe/QuickBooks/Google Calendar) + new: a test asserting every STUB-status adapter actually raises/reports NOT_CONNECTED (guards against `KLAROS_DO_NOT_BUILD_YET.md` §9 regressing silently) | Existing, expanded | Yes |
| Discovery | New: adaptive-question-capping test (never exceeds configured max), capability-critical-Unknown-never-defaulted test | New | Yes |
| Recommendation | New: every generated `Recommendation` carries all 8 required fields (WHY/WHAT/DEPENDENCIES/COST/REQUIRED-OR-OPTIONAL/ALTERNATIVES/CONFIDENCE/SOURCE) — a schema-completeness test, not a quality test | New | Yes |
| Blueprint | New: section-schema validation tests, claim confirm/reject state-machine tests, completion-criteria test (min-bar sections + no capability-critical Unknown) | New | Yes |
| Website generation | New: component-registry-only assertion (no test can construct a `WebsitePage` referencing a component not in the fixed registry — this test is the mechanical enforcement of "never arbitrary code"), form-target-allowlist test | New | Yes |
| Domain extensions | New, per vertical: Medical Tourism and Dropshipping table/FK-integrity tests, "core services never import a vertical by name" static-analysis check (grep-based CI check, not just a design rule) | New | Yes |
| Frontend | New Vitest/RTL component tests + Playwright E2E for the new multi-page flow (Discovery→Launch) | New | Yes (from Phase 0 scaffold onward, coverage grows incrementally) |
| E2E | Playwright, full flows for both validation scenarios (Medical Tourism, Dropshipping) | New | Run on staging deploy, not every PR (cost) |
| Security | Prompt-injection test suite (crafted inputs attempting to make an agent call an out-of-scope tool), SSRF-allowlist test for website forms, credential-encryption boundary test | New | **Mandatory pre-autonomy gate item**, per KLAROS_FINAL_SECURITY_MODEL.md |
| Regression | Existing backend suite + new frontend suite, run in full on every PR | Existing + new | Yes |
| Tenant isolation (general, beyond RLS-specific) | New: every new service-layer method that queries a tenant-scoped table has a paired isolation test | New | Yes |

## Pre-autonomous-execution test gate (upheld, made concrete)

No Agent may exceed Recommend tier until these five pass in CI: (1) plan-validation tests (the agent's proposed tool call validates against its declared permissions and the tool's schema before execution), (2) tenant-isolation tests (for every table the agent's tools touch), (3) hard-limit tests (rate/concurrency/chain-depth ceilings actually reject over-limit attempts), (4) prompt-injection tests, (5) one staging dry run reviewed by a human. This is the same gate stated in KLAROS_FINAL_SECURITY_MODEL.md, restated here as the testing-layer's own acceptance criterion so the two documents cannot drift.

## CI integration

All of the above are wired into the 8-stage pipeline in KLAROS_FINAL_DEPLOYMENT_ARCHITECTURE.md — stages 4 (Unit) and 5 (Integration) run the full matrix above per PR; stage 6 (Security checks) runs the prompt-injection/SSRF/credential-boundary suite once those subsystems exist; the RLS-specific tenant-isolation suite is explicitly called out as non-skippable in stage 5, matching KLAROS_FINAL_SECURITY_MODEL.md's testing-strategy requirement verbatim.
