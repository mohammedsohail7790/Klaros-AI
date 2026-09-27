# Klaros AI — Master Implementation Roadmap

Covers item FF. Sliced into independently shippable increments, each with Goal/Scope/Backend/Database/Frontend/AI/Security/Tests/Deployment/Dependencies/Acceptance-criteria/Rollback. Phase 0 and Phase 1 are detailed fully in their own documents; this document carries the full roadmap through Phase 11 at the same level of concreteness, avoiding vague multi-month "build everything" phases.

## Phase 0 — Governance/Security/CI Foundation
See KLAROS_PHASE_0_IMPLEMENTATION_PLAN.md in full. **Blocks everything else.**

## Phase 1 — Business Domain Foundation (catalog/registry tables)
See KLAROS_PHASE_1_IMPLEMENTATION_PLAN.md in full. **Blocks Phases 2-10.**

## Phase 2 — Business Discovery
- **Goal:** free-text business description → adaptive, capped follow-up questions → confirmed answers persisted.
- **Scope:** `DiscoverySession`/`DiscoveryTurn` only — no `BusinessBlueprint` tables yet (per KLAROS_ARCHITECTURE_RECONCILIATION.md #4).
- **Backend:** new `business_discovery` service + router (`/api/v1/business-discovery`, KLAROS_FINAL_API_ARCHITECTURE.md); adaptive-question generation via existing `ai_provider.py` abstraction (structured-output mode), degrading to a fixed structured-choice flow under `DeterministicAIProvider`.
- **Database:** `DiscoverySession`, `DiscoveryTurn` (Phase-2-only migration, per sequencing in KLAROS_FINAL_DATABASE_ARCHITECTURE.md).
- **Frontend:** new `/discovery` page.
- **AI:** question generation is capped (default 8 questions), targets only `REQUIRED_CAPABILITIES` gaps, never silently defaults capability-critical Unknowns.
- **Security:** RLS-on-day-one for both new tables (per Phase 0 policy).
- **Tests:** question-capping test, capability-critical-Unknown-never-defaulted test, deterministic-fallback test.
- **Deployment:** no new deploy units.
- **Dependencies:** Phase 0, Phase 1.
- **Acceptance criteria:** a free-text description of "a medical tourism business connecting patients with hospitals in Turkey and India" produces a completed `DiscoverySession` with no un-surfaced capability-critical Unknowns, within the question cap.
- **Rollback:** feature-flag the `/discovery` route off; drop tables (no downstream dependents yet since Blueprint ships in Phase 3).

## Phase 3 — Business Blueprint
- **Goal:** confirmed discovery answers (and direct human edits) become a versioned, auditable Blueprint.
- **Scope:** `BusinessBlueprint`, `BlueprintSection`, `BlueprintClaim` (KLAROS_FINAL_BUSINESS_BLUEPRINT.md).
- **Backend:** promotion service (`DiscoverySession` → `BlueprintClaim` rows), confirm/reject tool calls, section-edit endpoint.
- **Database:** the 3 tables above, RLS-on-day-one.
- **Frontend:** new `/blueprint` page.
- **AI:** none new — Blueprint itself is not AI-generated content, only claim *proposals* from Discovery are.
- **Security:** new `MANAGE_BLUEPRINT` permission (KLAROS_ARCHITECTURE_RECONCILIATION.md #2).
- **Tests:** section-schema validation, claim state-machine, completion-criteria tests.
- **Deployment:** none new.
- **Dependencies:** Phase 2 (consumes `DiscoverySession` output).
- **Acceptance criteria:** a Blueprint reaches ACTIVE status for the Medical Tourism validation scenario with all min-bar sections confirmed.
- **Rollback:** feature-flag `/blueprint`; existing `DiscoverySession` data remains valid and re-promotable once re-enabled.

## Phase 4 — Recommendation Engine
- **Goal:** Blueprint claims + catalog data → recommendations with full WHY/WHAT/DEPENDENCIES/COST/REQUIRED-OR-OPTIONAL/ALTERNATIVES/CONFIDENCE/SOURCE.
- **Scope:** `Recommendation` table + plugin-based generation (no hardcoded per-vertical `if` branches — a registered plugin function per `VerticalExtension`, per KLAROS_FINAL_DOMAIN_MODEL.md).
- **Backend:** `recommendation_service`, plugin registry keyed by `DomainDefinition.id`.
- **Database:** `Recommendation` table.
- **Frontend:** new `/recommendations` page.
- **AI:** may use `ai_provider.py` for the `why`/`what` narrative text, never for the structural decision of *what* to recommend (that's rule/plugin-derived, keeping recommendations auditable and reproducible).
- **Security:** recommendations respect existing role permissions on their target type (e.g. accepting an integration recommendation still requires `MANAGE_INTEGRATIONS`).
- **Tests:** schema-completeness test (all 8 fields present on every generated recommendation).
- **Deployment:** none new.
- **Dependencies:** Phase 3 (reads Blueprint claims), Phase 1 (reads `IntegrationProviderCatalog`).
- **Acceptance criteria:** Dropshipping validation scenario produces recommendations for at minimum a payment processor and a shipping-tracking capability, each fully-specified per the 8-field schema.
- **Rollback:** feature-flag `/recommendations`; no destructive dependents.

## Phase 5 — Integration Marketplace (UI taxonomy)
- **Goal:** full CONNECTED/RECOMMENDED/REQUIRED/OPTIONAL/AVAILABLE/NOT_CONNECTED/COMING_SOON/STUB/CUSTOM taxonomy live in `/settings/integrations`.
- **Scope:** frontend-only extension of the existing 872-line page, consuming Phase 1's catalog + Phase 4's recommendations.
- **Backend:** `GET /integrations/catalog` merge logic (KLAROS_FINAL_API_ARCHITECTURE.md).
- **Database:** none new (reuses Phase 1's catalog table).
- **Frontend:** `/settings/integrations` extended, not rebuilt.
- **Security:** STUB-never-renders-as-CONNECTED enforced at the derivation layer (KLAROS_FINAL_INTEGRATION_MODEL.md).
- **Tests:** derivation-rule test (every combination of catalog status × connection status produces the correct UI status).
- **Dependencies:** Phase 1, Phase 4.
- **Acceptance criteria:** all 6 confirmed-stub providers (Xero, Google Ads, Meta Ads, GBP, ServiceTitan, Jobber) render distinguishably from the 3 real ones in the UI.
- **Rollback:** revert the merge-logic endpoint; page falls back to Phase-1-era catalog-only read.

## Phase 6 — Website Builder
- **Goal:** Blueprint → published website via the fixed-component-registry pipeline.
- **Scope:** `Website`/`WebsiteVersion`/`WebsitePage`/`WebsiteSection`/`WebsiteFormBinding`, component registry, preview renderer, publish flow reusing `ApprovalRequest`.
- **Backend/Frontend/AI/Security:** full detail in KLAROS_FINAL_FRONTEND_ARCHITECTURE.md §Website Builder.
- **Deployment:** new website-runtime deploy unit (isolated origin).
- **Tests:** component-registry-only assertion, form-target-allowlist test.
- **Dependencies:** Phase 3 (Blueprint drives Website Requirements), Phase 0 (approval flow, RLS).
- **Acceptance criteria:** both validation scenarios (Medical Tourism, Dropshipping) can reach a published, previewable website whose forms submit only to allowlisted endpoints.
- **Rollback:** publishing a prior `WebsiteVersion` is the built-in rollback; disabling the website-runtime deploy unit entirely is the full-feature rollback.

## Phase 7 — Agent Runtime (hard-gated by Phase 0's pre-autonomy security gate)
- **Goal:** governed, tiered, auditable Agents built on the existing `ToolRegistry`/`AIExecutionService`.
- **Scope:** `Agent`/`AgentVersion`/`AgentExecution`/`AgentToolPermission`, the 2 new pre-checks in `ToolRegistry.execute()`, `AgentExecutionService`.
- **Backend/Security:** full detail in KLAROS_FINAL_AGENT_MODEL.md, KLAROS_FINAL_SECURITY_MODEL.md.
- **Database:** the 4 tables + `AIInvocationLog.agent_execution_id` additive column.
- **Frontend:** new `/agents` page.
- **Tests:** the full agent-runtime test layer in KLAROS_FINAL_TESTING_ARCHITECTURE.md, including the mandatory pre-autonomy gate suite.
- **Deployment:** none new (runs in-process within existing backend/worker).
- **Dependencies:** Phase 0 (hard gate — no agent exceeds Recommend tier until the gate's 4 conditions hold), Phase 1 (tool catalog for permission configuration UI), Phase 3 (agents reference Blueprint sections).
- **Acceptance criteria:** an agent scoped to lead-qualification tools only, at Recommend tier, correctly proposes but never autonomously executes a CRM update, with full audit trail.
- **Rollback:** pause all agents (existing `ai_paused` kill-switch already covers this at the org level; per-agent pause is Phase 7's own new capability); drop new tables if rolled back pre-production.

## Phase 8 — Workflow Generation
- **Goal:** business requirements compile into either the existing Automation Engine or Temporal workflow definitions.
- **Scope:** `Workflow`/`WorkflowVersion` spec authoring + compiler, new `/workflows` frontend page (distinct from `/automations`, per KLAROS_FINAL_FRONTEND_ARCHITECTURE.md).
- **Backend:** compiler targets existing `automation_service.py` (simple trigger-chains) or new Temporal workflow definitions registered on the existing worker (durable/compensating workflows) — never a third engine.
- **Tests:** compiled-output-executes-correctly tests against both target engines.
- **Dependencies:** Phase 3 (Blueprint Workflows section), Phase 7 (workflow steps may invoke an Agent).
- **Acceptance criteria:** the "new lead → qualification agent → CRM update → email → calendar booking → human notification" example workflow compiles and executes correctly end-to-end in staging.
- **Rollback:** disable compiled workflow's trigger; underlying `Automation`/Temporal definitions can be individually paused via existing mechanisms.

## Phase 9 — Medical Tourism domain extension
- **Goal:** full vertical validated end-to-end (KLAROS_VALIDATION_SCENARIOS.md).
- **Scope:** `Provider`, `ProviderCredential`, `Procedure`, `ProviderProcedure`, `PatientLead`, `Consultation`, `ReferralCommission` + `ReferralReward.currency`/`commission_basis` additive columns.
- **Dependencies:** Phase 1 (`VerticalExtension` registry), Phase 3 (Blueprint sub-schema), Phase 8 (vertical-specific workflows), parallelizable with Phase 10.
- **Acceptance criteria:** full AA validation scenario (KLAROS_VALIDATION_SCENARIOS.md) passes with no missing-capability gaps beyond explicitly documented ones.
- **Rollback:** disable the vertical via `OrganizationVerticalExtension`; tables remain but are unreferenced.

## Phase 10 — Dropshipping domain extension
- **Goal:** full vertical validated end-to-end.
- **Scope:** `Supplier`, `Product`, `SKU`, `InventorySnapshot`, `Order`, `OrderLine`, `SupplierOrder`, `Shipment`, `OrderReturn` — genuinely new relational domain, no `Vendor`/`VendorBill` reuse.
- **Dependencies:** Phase 1 only (no shared dependency with Phase 9 — genuinely parallelizable).
- **Acceptance criteria:** full BB validation scenario passes.
- **Rollback:** disable the vertical via `OrganizationVerticalExtension`.

## Phase 11 — Production Hardening (ongoing, not a fixed-duration phase)
- **Goal:** close remaining UNKNOWN — REQUIRES VERIFICATION items accumulated throughout this document set (Redis namespacing, Temporal workflow-ID tenant-leakage, object-storage encryption-at-rest, SendGrid independent verification, backend lint/typecheck tooling, Alembic connection-role confirmation), expand RLS to the remaining ~185 tenant-scoped tables beyond Phase 0's initial 5, expand frontend test coverage incrementally as features ship.
- **Dependencies:** runs continuously alongside Phases 2-10, not strictly after them.
- **Acceptance criteria:** no open UNKNOWN item remains without either a resolution or an explicit, dated re-verification plan.
