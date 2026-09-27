# Klaros — Implementation Roadmap

Status: proposal only. Nothing in this roadmap has been executed. Priority definitions used throughout: **P0** — must happen before expanding AI execution or exposing production customers to new autonomy; **P1** — required for the intended product to exist at all; **P2** — important, can follow the core platform; **P3** — future optimization, not blocking.

## Phase 0 — Security & Foundation (P0)

**Objective**: close the structural gaps that make everything after this phase unsafe to build on, before adding a single new tenant-facing table.

- Existing components reused: `TenantScopedMixin`, `CurrentUser.tenant_id` dependency chain, `ToolRegistry`, `AuditLog`.
- New: RLS Phase A instrumentation (`KLAROS_SECURITY_EVOLUTION_PLAN.md` §2), CI pipeline (lint/test/migration-dry-run/build, `KLAROS_DEPLOYMENT_EVOLUTION_PLAN.md` §3), staging environment, `/health` endpoint confirmation, frontend test tooling adoption (Vitest/Playwright, `KLAROS_FRONTEND_EVOLUTION_PLAN.md` §7).
- Database work: none (RLS Phase A is instrumentation, not schema).
- Backend work: `SET LOCAL app.current_tenant_id` wiring; resolve `automation.py`/`automations.py` naming ambiguity.
- Security work: full RLS rollout plan execution begins (Phase A only in this phase).
- Tests: tenant-isolation regression tag established as a CI gate.
- Dependencies: none — this phase blocks everything else.
- Risks: RLS instrumentation revealing a code path that doesn't set tenant context correctly (e.g. a background job) — treat as a finding to fix, not a reason to abandon RLS.
- Exit criteria: CI green on lint/test/build for every PR; staging environment reachable and exercised; RLS Phase A logging shows 100% correct tenant-context-setting for one full deploy cycle; `pytest` full suite run to completion with a known pass/fail baseline (closing the original audit's "collection ≠ passing" gap).

## Phase 1 — Business Domain Foundation (P1)

**Objective**: establish the extensibility pattern and reference/catalog tables everything else plugs into.

- Reused: `Organization`, code-level RBAC enum pattern.
- New: `VerticalExtension`, `IntegrationProviderCatalog`, `ToolMetadata` tables (`KLAROS_DATABASE_EVOLUTION_PLAN.md` §2); catalog seeding migration for the 6 real + 6 stub + 3 webhook-normalizer providers already in the codebase.
- Backend: catalog CRUD (admin-only for now), `GET /integrations/catalog` read endpoint.
- Tests: catalog-seeding data-migration test; drift-check CI job (`KLAROS_INTEGRATION_MARKETPLACE_SPEC.md` §7).
- Dependencies: Phase 0 (RLS instrumentation pattern reused for these tables from day one, no bypass period needed since they're new).
- Risks: low — purely additive reference data.
- Exit criteria: catalog queryable and accurately reflects verified current integration reality (no stub shown as real).

## Phase 2 — Business Discovery (P1)

- Reused: `ai_provider.py`'s `AI_PROVIDER`/`DeterministicAIProvider` abstraction, `KnowledgeFile`-writing pattern from the existing onboarding wizard.
- New: `DiscoverySession`, `BlueprintClaim` (Blueprint's own table lands next phase but `BlueprintClaim` is created here since Discovery produces it — **UNKNOWN whether to sequence `BlueprintClaim` creation into Phase 2 or 3**; recommend creating both `BusinessBlueprint` skeleton and `BlueprintClaim` together in Phase 2 to avoid an awkward mid-phase dependency, even though the brief separates Discovery/Blueprint conceptually).
- Backend: `/business-discovery/*` routes, extraction/gap-check service logic.
- Frontend: `/discovery` route.
- AI work: extraction + adaptive-question-generation prompts, deterministic-fallback behavior (`KLAROS_BUSINESS_DISCOVERY_SPEC.md` §6).
- Tests: gap-detection unit tests, deterministic-fallback behavior test, adaptive-question-cap test.
- Dependencies: Phase 0, Phase 1 (capability vocabulary from catalog).
- Risks: over-asking (mitigated by the hard cap and gap-only questioning design); under-extracting from vague descriptions (mitigated by explicit `Unknown` claims rather than silent defaults for capability-critical fields).
- Exit criteria: both test businesses' one-line descriptions (`KLAROS_MEDICAL_TOURISM_VALIDATION.md`/`KLAROS_DROPSHIPPING_VALIDATION.md`) produce a sensible gap list and ≤8 follow-up questions each.

## Phase 3 — Business Blueprint (P1)

- New: `BusinessBlueprint`, `BlueprintSection` tables; confirm/reject claim endpoints; `CompanyMemory` mirroring on confirm.
- Frontend: `/blueprint` route (Business Map view).
- Dependencies: Phase 2.
- Exit criteria: both test businesses reach `BusinessBlueprint.status=ACTIVE` with all minimum-bar sections complete.

## Phase 4 — Recommendation Engine (P1)

- New: `Recommendation` table, capability-intersection matching logic (deterministic, not per-recommendation LLM calls).
- Frontend: `/recommendations` route.
- Dependencies: Phase 1 (catalog), Phase 3 (Blueprint).
- Exit criteria: both test businesses' expected recommendation sets (validated in the two vertical validation documents) are produced correctly, including correctly *not* recommending `STUB`-status providers as if functional.

## Phase 5 — Integration Marketplace (P1)

- Frontend: `/settings/integrations` extended with catalog badges.
- Dependencies: Phase 1, Phase 4.
- Exit criteria: UI never renders a `STUB`/`WEBHOOK_NORMALIZER` provider indistinguishably from a `REAL`+`CONNECTED` one (closes the original audit's P2 finding definitively).

## Phase 6 — Website Builder (P1/P2)

**Objective**: largest single net-new build item; sequence after Blueprint/Recommendation so generated sites are grounded in confirmed business facts, not guesses.

- New: full `Site`/`SiteVersion`/... table family, initial component registry (5–8 core components), `website-runtime` deploy unit, new narrow `public_appointment-requests` endpoint.
- Object storage: real S3 adapter must land here at the latest (generated assets need real storage, not local disk, for a production multi-instance deploy — dependency on Phase 0's staging environment to test against).
- Tests: spec-validation tests, component-registry snapshot tests, form-submission integration test against existing `public_leads`.
- Dependencies: Phase 3 (Blueprint), Phase 0 (S3 adapter, separate deploy unit).
- Risks: component registry becoming a bottleneck (new component types require an engineering change, not a tenant/AI action — an accepted tradeoff per ADR-005) — mitigate by shipping enough core components to cover both test businesses' validation walkthroughs before calling this phase done.
- Exit criteria: both test businesses can reach a published, working lead-capture website end to end in staging.

## Phase 7 — Agent Runtime (P1, hard-gated by Phase 0's security work)

- New: full Agent entity model (`KLAROS_AI_AGENT_ARCHITECTURE.md` §2), `AgentExecutionService`, plan validation, hard limits, autonomy tiers.
- Reused, unchanged: `AIExecutionService`, `ToolRegistry`, `ApprovalRequest`, `AuditLog`, `AIInvocationLog` (+ one additive FK column).
- Security work: this phase is where `KLAROS_SECURITY_EVOLUTION_PLAN.md` §7's explicit pre-autonomy gate is enforced — RLS Phase C must cover every table any shipped agent's allowlist touches before that agent can exceed `Recommend` tier.
- Tests: full AI-contract/agent-execution/prompt-injection suite (`KLAROS_TESTING_STRATEGY.md` §1–§2).
- Dependencies: Phase 0 (hard-blocked), Phase 3 (Blueprint context), Phase 1 (`ToolMetadata`).
- Exit criteria: at least one agent runs a real multi-step execution in staging at `Execute-approved` tier with full audit trace, zero cross-tenant test failures, all hard limits demonstrated to actually trigger (not just configured).

## Phase 8 — Workflow Generation (P1/P2)

- New: bounded AI-plan-to-`Automation`-specification compiler, human-approval gate before materialization.
- Reused: `Automation`/`AutomationVersion` schema and execution engine, unchanged.
- Dependencies: Phase 7 (an agent, or a narrower discovery-time proposal step, produces the candidate workflow spec).
- Exit criteria: both test businesses' example workflows (walked in the vertical validation docs) can be proposed and, on approval, materialize as a working `Automation`.

## Phase 9 — Medical Tourism Vertical (P2)

- New: `Provider`/`ProviderCredential`/`Procedure`/`ProviderProcedure`/`PatientLead`/`Consultation`/`ReferralCommission`, additive `ReferralReward` currency columns.
- Frontend: `/providers`, `/procedures`.
- Website: `ProviderDirectoryCard` component.
- Dependencies: Phase 1 (extension pattern), Phase 3 (Blueprint sub-schema), Phase 6 (website component), Phase 7 (optional matching agent).
- Exit criteria: `KLAROS_MEDICAL_TOURISM_VALIDATION.md` walkthrough passes end to end in staging.

## Phase 10 — Ecommerce/Dropshipping Vertical (P2)

- New: `Supplier`/`Product`/`SKU`/`InventorySnapshot`/`Order`/`OrderLine`/`SupplierOrder`/`Shipment`/`OrderReturn`.
- Frontend: `/products`, `/orders`, `/suppliers`.
- Website: `ProductGrid` component.
- Dependencies: same as Phase 9, independent of it (can run in parallel with Phase 9 given the shared extension pattern).
- Exit criteria: `KLAROS_DROPSHIPPING_VALIDATION.md` walkthrough passes end to end in staging.

## Phase 11 — Production Hardening (P0/P1, ongoing)

- RLS Phase C completion across all existing 208 + new tables, prioritized by sensitivity.
- Load testing (`KLAROS_TESTING_STRATEGY.md` §1), failure-injection testing, full observability review (`KLAROS_TARGET_ARCHITECTURE.md` §10).
- Promotion gate defined for CI→staging→production auto-deploy (deferred from Phase 0 deliberately, per `KLAROS_DEPLOYMENT_EVOLUTION_PLAN.md` §3).
- Exit criteria: every P0 item across all documents in this analysis is closed.

## Priority classification — summary rationale

**P0**: multi-tenancy DB backstop (RLS), CI pipeline, staging environment, agent execution hard limits and plan validation — each is a precondition for *any* of the P1 work being safe to expose to real tenants, per direct evidence that the current autonomy mechanism is inert and there is no DB-level tenant backstop.

**P1**: Discovery, Blueprint, Recommendation, Integration Marketplace, Website Builder, Agent Runtime, Workflow Generation — these collectively *are* the product described in the brief; without them Klaros remains only the existing field-service operations SaaS.

**P2**: the two vertical extensions — required to prove the extensibility pattern and serve the two named validation businesses, but the platform is coherent and usable (for a generic services business) without them.

**P3**: full metrics/tracing observability stack beyond the data-layer approach in `KLAROS_TARGET_ARCHITECTURE.md` §10, MFA/SSO for end-user login, a third/fourth vertical extension, any move off Docker Compose — explicitly deferred per `KLAROS_DO_NOT_BUILD_YET.md`.

## Cross-references

Every phase above links to its full spec document; `KLAROS_ARCHITECTURAL_DECISIONS.md` for the decisions underpinning Phase 0/7/6 in particular; `KLAROS_GAP_ANALYSIS.md` for the evidence backing each phase's necessity.
