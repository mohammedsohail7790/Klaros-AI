# Klaros — Testing Strategy

Status: proposal only. No tests were added, run, or modified. Baseline (verified): backend has 182 test files, 1,417 tests collected by `pytest --collect-only` (real, executed during the original audit pass; pass/fail status of a full run was not verified in either pass — **UNKNOWN — REQUIRES VERIFICATION**, recommend running `pytest` to a completed result before relying on "1,417 tests" as a quality signal). Frontend has zero tests: no test script in `package.json`, no test library dependency (verified by direct read). No CI pipeline exists for this repo.

## 1. Test layers required, mapped to what's new vs. what exists

| Layer | Current state | Target requirement |
|---|---|---|
| Backend unit | Real, extensive (sampled tenant-isolation tests found, e.g. `test_warranties_isolated_per_tenant` per original audit) | Extend the same pattern to every new service (Discovery, Blueprint, Recommendation, Agent, Website, domain extensions) |
| Backend integration/API | Real (`TestClient`-based, per sampled `test_warranties_over_http`) | Same pattern for all new routers |
| Database | Implicit via ORM tests against SQLite fallback; pgvector-specific paths only exercised where Postgres is used | New: an explicit pgvector-path test tier (CI service container, not SQLite) — the existing knowledge-retrieval dual-path design (`knowledge_retrieval_service.py`) already has this gap: SQLite CI never touches the real HNSW query |
| **Tenant isolation** | Real, sampled pattern exists (`*_isolated_per_tenant` naming convention) | **Mandatory for every new table** — a dedicated, always-run test tag (e.g. `pytest -m tenant_isolation`) required before merge for any PR touching a new table; this is the single most important net-new testing requirement given §1.2 of `KLAROS_GAP_ANALYSIS.md` |
| AI-contract | Not applicable today (no agent exists) | New: deterministic tests asserting an `AgentExecution`'s plan validation correctly rejects (a) a tool not in the agent's `AgentToolPermission`, (b) a malformed input against the tool's schema, (c) a plan exceeding `max_steps` — these are pure logic tests, no live LLM call needed, since plan validation is deterministic by design (`KLAROS_AI_AGENT_ARCHITECTURE.md` §6 step 4) |
| Tool-authorization | Real today, implicit in the RBAC/policy test coverage the audit sampled | Extend to cover the new `ToolMetadata` fields (`risk_tier`, `financial_risk`, `mutates_state`) — a test per new tool confirming it defaults to a safe policy (never `AUTO` for a destructive action) |
| Agent-execution | N/A today | New: end-to-end tests running a full plan→validate→approve→execute→audit cycle against a fake/mocked AI provider (deterministic mode, reusing the existing `DeterministicAIProvider` pattern already in `ai_provider.py` — a real, existing test seam, not a new mocking framework) |
| Workflow | Real for existing Temporal workflows (per audit's automation-engine tests) | New workflow definitions (`AgentExecutionWorkflow` etc.) tested the same way, using Temporal's test framework (`temporalio.testing`) if the existing test suite already uses it (**UNKNOWN — REQUIRES VERIFICATION**, not confirmed in either audit pass whether Temporal's `WorkflowEnvironment` test harness is currently used) |
| Integration-sandbox | Real for Stripe (webhook signature verification is itself testable deterministically); QuickBooks/Google Calendar likely use recorded fixtures or a sandbox account (**UNKNOWN**, not verified) | New marketplace catalog tests: confirm `IntegrationProviderCatalog.implementation_status=REAL` entries actually have a corresponding client file (the CI drift-check proposed in `KLAROS_INTEGRATION_MARKETPLACE_SPEC.md` §7) |
| Website-generation | N/A today | New: schema-validation tests for every `Component` registry entry (a spec referencing an unknown component type must fail validation, per `KLAROS_WEBSITE_BUILDER_SPEC.md` §5) |
| Website-rendering | N/A today | New: visual/snapshot tests for the component registry (frontend test layer, §3) |
| E2E | **UNKNOWN — REQUIRES VERIFICATION** whether any E2E tooling (Playwright/Cypress) exists; not found in `frontend/package.json` (confirmed no test deps at all) | New: at minimum, one E2E path per new critical flow — Discovery→Blueprint→Recommendation→Agent-approval, and Website preview→publish |
| Security | Not independently assessed as a test *suite* in either audit pass | New: RLS-bypass-attempt tests (confirm a session with the wrong `app.current_tenant_id` cannot read another tenant's row even with a crafted query), prompt-injection tests against Context Assembly (feed a `KnowledgeChunk`/`CompanyMemory` value containing an injected instruction, assert the planning call doesn't act on it — reusing the existing fenced-DATA-block pattern's own testability) |
| Regression | Existing 1,417 backend tests serve this role today | Full-suite run required in CI (§`KLAROS_DEPLOYMENT_EVOLUTION_PLAN.md` §3) before merge, not just on-demand |
| Load | None found today | New: load test for `AgentExecutionService` under concurrent executions (contention on `AgentExecutionStep` writes, Temporal worker throughput) before enabling `Execute-autonomous` tier broadly |
| Failure-injection | None found today | New: simulate AI provider outage, integration API failure, Temporal unreachable — assert each degrades per `KLAROS_EXECUTIVE_ARCHITECTURE_SUMMARY.md` appendix's failure-mode table, not silently |
| Prompt-injection | None found today (no agent exists to inject into; the three existing narrow AI call sites already have a mitigation pattern, §above) | New, explicit test suite — required before any agent reaches `Execute-approved` tier per `KLAROS_SECURITY_EVOLUTION_PLAN.md` §7 |

## 2. What must be tested before allowing any autonomous AI execution (explicit gate)

Per the brief's direct requirement, this is the release gate, not a suggestion: (1) plan-validation unit tests (deterministic rejection of hallucinated/unauthorized/malformed tool calls); (2) tenant-isolation tests for every table an agent's allowlist can touch; (3) hard-limit tests (financial/communication/integration/execution caps actually trigger, not just exist in a config schema); (4) prompt-injection tests against every Context Assembly input path; (5) at least one full staging-environment agent-execution dry run with a human reviewing every step. Until all five pass, no `Agent` may be set above `Recommend` tier in production, regardless of what a tenant's `AgentPolicy` requests (the tier ceiling is a deploy-time gate, not just a UI default).

## 3. Frontend testing (net-new tooling decision)

**Recommendation** (decision owner: engineering team at Phase 0 kickoff, not made by this document): adopt Vitest + React Testing Library for component/unit tests (lighter-weight than Jest for a Next.js 16/Vite-adjacent toolchain) and Playwright for E2E (covers both the marketing/app frontend and, once it exists, the separate `website-runtime` service's generated-site rendering). Start coverage with the highest-consequence new surfaces first: Agent builder/approval UI, Website publish flow, Blueprint claim-confirmation UI — not a blanket retrofit of all 57 existing pages, which is lower urgency given they've operated without tests to date without a demonstrated defect (per audit, no confirmed P0 frontend bug).

## 4. CI integration

See `KLAROS_DEPLOYMENT_EVOLUTION_PLAN.md` §3 for the full pipeline; testing-strategy-specific requirement: the tenant-isolation tag (§1) and the prompt-injection suite (§1) are non-skippable merge gates for any PR touching a new table or a new AI call site, enforced by CI branch protection, not code review discipline alone.

## Cross-references

`KLAROS_GAP_ANALYSIS.md` §3.11, `KLAROS_SECURITY_EVOLUTION_PLAN.md` §7, `KLAROS_AI_AGENT_ARCHITECTURE.md` §5–§6, `KLAROS_DEPLOYMENT_EVOLUTION_PLAN.md` §3.
