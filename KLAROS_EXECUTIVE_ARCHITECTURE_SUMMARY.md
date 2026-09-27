# Klaros — Executive Architecture Summary

Status: this document is a synthesis of the other 19 documents produced in this analysis; it contains no new claims not already evidenced there. This entire analysis is read-only: no application code, database schema, dependencies, environment variables, or deployment configuration were changed to produce it.

## What Klaros is today

A mature, real, tested vertical field-service/home-services operations SaaS — comparable in domain to ServiceTitan/Jobber — with CRM (leads/customers/appointments), job/work-order lifecycle management, quoting, internal-attestation contracts, invoicing/AR/payments, an 18-class marketing suite, an 18-class customer-retention suite, compliance license tracking, a real multi-tenant RBAC backend (7 roles, ~80 permissions), a governed 57+-tool AI-execution boundary with permission/policy/approval/audit gating, an AI voice receptionist (Twilio-cascaded and OpenAI-Realtime engines), a pgvector-backed Knowledge/RAG layer, a real Temporal-backed automation engine, and a real event bus. Backend: 55 API router modules, ~208 SQLAlchemy models, 40 Alembic migrations, 1,417 collected pytest tests. Frontend: Next.js 16, 57 route pages, one hand-written typed API client. It is **not** an AI business-discovery/recommendation/website-builder platform — that layer does not exist in the code at all today.

## What it needs to become

The additive platform described across `KLAROS_TARGET_ARCHITECTURE.md`: a Business Discovery → Business Blueprint → Recommendation Engine → Integration Marketplace → Website Builder → General Agent Runtime → (existing, governed) Tool Execution → Business Operations pipeline, extensible to new verticals (validated here against Medical Tourism and Dropshipping) without hard-coding around any one of them.

## What existing architecture is preserved

Everything in the "today" paragraph above, unchanged: FastAPI, PostgreSQL+pgvector, Redis, Temporal, the event bus, `ToolRegistry`/`AIExecutionService`, RBAC, `ApprovalRequest`/`AuditLog`/`AIInvocationLog`, `CompanyMemory`, `Lead`/`Customer`/`Appointment`, Stripe/QuickBooks/Google Calendar/Twilio clients, the frontend API-client pattern. See `KLAROS_GAP_ANALYSIS.md` §2 for the full subsystem-by-subsystem reuse table — nearly everything is `Preserve`/`Extend`, almost nothing is `Refactor`/`Replace` (see `KLAROS_DO_NOT_BUILD_YET.md` for why).

## Single most important finding from the gap analysis

**`Organization.autonomy_level` (the `LEVEL_0`..`LEVEL_4` field the original audit brief assumed might be a working autonomy control) is stored but functionally inert — its own in-code comment states "remains unenforced/decorative," and a direct verification pass confirmed zero executing code anywhere in the backend reads it.** The real, working AI-governance mechanism today is a *tool-level*, not agent-level, `ActionPolicy` (`AUTO`/`APPROVAL_REQUIRED`/`BLOCKED`) plus a binary `ai_paused` kill switch — already correct and already shared identically between human and AI callers, but coarser than an autonomy scale and with no per-agent concept at all (because no agent concept exists). This single finding drives the entire design of `KLAROS_AI_AGENT_ARCHITECTURE.md` §3–§4: autonomy must be built new, at the `Agent` level, composed with (never assumed to already exist in) the tool-level policy layer.

## What Phase 1 of the roadmap should build, and why

Per `KLAROS_IMPLEMENTATION_ROADMAP.md`, **Phase 0 (Security & Foundation)** must complete before Phase 1's business-domain work, and within it the highest-leverage single item is **RLS instrumentation + a CI pipeline + a staging environment** — not because they are exciting, but because every subsequent phase (Discovery, Blueprint, Agent Runtime) adds new tenant-scoped tables and new AI-execution surface area, and today there is (a) no database-level backstop against a missed tenant filter, and (b) no automated gate catching a regression before merge. Building Discovery/Blueprint/Agents first, on this foundation, would mean every new feature inherits the same unaudited-tenant-isolation risk the original audit already flagged as its top P1 finding — compounding a known risk rather than closing it. Phase 1 proper (Business Domain Foundation: `VerticalExtension`/`IntegrationProviderCatalog`/`ToolMetadata` catalog tables) is the first business-facing work, chosen because every later phase (Recommendation, Marketplace, both verticals) depends on this catalog/extension-registry pattern existing first.

## Biggest risks

See the top-10 list below and the full failure/edge-case appendix.

## Implementation sequence

Phase 0 (Security/Foundation) → Phase 1 (Business Domain Foundation) → Phase 2 (Discovery) → Phase 3 (Blueprint) → Phase 4 (Recommendation) → Phase 5 (Integration Marketplace) → Phase 6 (Website Builder) → Phase 7 (Agent Runtime, hard-gated by Phase 0) → Phase 8 (Workflow Generation) → Phase 9/10 (Medical Tourism / Dropshipping, parallelizable) → Phase 11 (Production Hardening, ongoing). Full detail, exit criteria, and dependency reasoning in `KLAROS_IMPLEMENTATION_ROADMAP.md`.

---

## Twenty final executive questions

**1. What is Klaros today?** See above — a real field-service operations SaaS with a governed AI-tool layer, not yet an AI business-builder.

**2. What should it become?** The Discovery→Blueprint→Recommendation→Marketplace→Website→Agent→Tools→Operations platform in `KLAROS_TARGET_ARCHITECTURE.md`, built additively on top of what exists.

**3. What existing architecture is preserved?** See "What existing architecture is preserved" above and `KLAROS_GAP_ANALYSIS.md` §2 in full.

**4. Single most important missing subsystem?** The Business Blueprint (`KLAROS_BUSINESS_BLUEPRINT_SPEC.md`) — every other new subsystem (Discovery's output, Recommendation's input, Website's requirements source, Agent's context) depends on it existing first; without it, Discovery has nowhere to write and Recommendation has nothing to read.

**5. Correct canonical business data model?** `BusinessBlueprint` (persisted) + `BlueprintSection` (JSONB, per-section, versioned) + `BlueprintClaim` (atomic Fact/Inference/Assumption/Requirement/Preference/Constraint/Decision/Unknown rows with confidence/evidence/source) — not a single JSON blob, not 24 fully-normalized tables. Full rationale in `KLAROS_BUSINESS_BLUEPRINT_SPEC.md` §2–§3 and ADR-001.

**6. Correct AI architecture?** Three narrow existing AI call sites remain unchanged; a new Agent Runtime sits strictly on top of the existing `AIExecutionService`/`ToolRegistry` boundary, never beside or instead of it. Planning is hybrid: LLM-proposed, deterministically validated before any execution. Full detail: `KLAROS_AI_AGENT_ARCHITECTURE.md`, ADR-002.

**7. How do agents interact with the existing ToolRegistry?** Every agent tool call goes through `AIExecutionService.request_tool_execution` exactly as it works today (unmodified), preceded by a new deterministic plan-validation step that checks the tool against a new per-agent `AgentToolPermission` allowlist — the agent never calls `ToolRegistry` directly, and the allowlist is enforced *before* the existing RBAC/policy/approval pipeline runs, as an additional, narrower gate, not a replacement for it.

**8. How do autonomy/approvals work?** Real, working autonomy is introduced at the `Agent` level (Observe/Recommend/Execute-approved/Execute-autonomous), composed with the existing tool-level `ActionPolicy`, never assuming the inert `Organization.autonomy_level` field already does this. Hard, tier-independent ceilings exist for financial/destructive actions. Approvals reuse the existing `ApprovalRequest`/`ApprovalStatus`/`ApprovalExecutionStatus` machinery via a new `AgentApproval` wrapper. Full detail: `KLAROS_AI_AGENT_ARCHITECTURE.md` §4–§5.

**9. How does website generation work?** Blueprint → Website Requirements → Site/Page/Component Specification (fixed, reviewed component registry, never generated code) → Content Specification (LLM fills only declared text slots) → Preview (same registry as production) → Validation → Human Approval → Publish, on an isolated `website-runtime` deploy unit. Forms submit into the existing `public_leads`-pattern endpoints, never a bespoke per-tenant backend. Full detail: `KLAROS_WEBSITE_BUILDER_SPEC.md`, ADR-005.

**10. How do integrations become recommendation-driven?** A new, tenant-independent `IntegrationProviderCatalog` table (capabilities, real `implementation_status` — never letting a stub look production-ready) is matched against `BusinessBlueprint`'s `REQUIRED_CAPABILITIES` list to produce evidenced `Recommendation` rows — additive to, never replacing, the existing real `IntegrationConnection` per-tenant connection model. Full detail: `KLAROS_INTEGRATION_MARKETPLACE_SPEC.md`, ADR-006.

**11. How to support medical tourism without hard-coding around it?** `Provider`/`Procedure`/`PatientLead`(extends `Lead`)/`Consultation`(extends `Appointment`)/`ReferralCommission`(extends `Referral`) as additive tables through the generic `VerticalExtension` pattern — core services (`LeadService`, `ToolRegistry`, `AgentExecutionService`) never reference "medical tourism" by name. Validated end-to-end in `KLAROS_MEDICAL_TOURISM_VALIDATION.md`.

**12. How to support dropshipping without hard-coding around ecommerce?** New `Product`/`SKU`/`Supplier`/`Order`/`OrderLine`/`InventorySnapshot`/`SupplierOrder` tables through the same generic extension pattern, explicitly never repurposing `Vendor`/`VendorBill` (verified to have no product/catalog concept). Validated end-to-end in `KLAROS_DROPSHIPPING_VALIDATION.md`.

**13. How do new verticals plug in?** Register a `VerticalExtension` row, add additive FK-referencing tables, register a Blueprint section sub-schema, register new narrow Tools, optionally register new Website components, contribute capability keys to the Recommendation matcher — zero core-service code changes required, demonstrated by the "Home Renovation Financing" third-vertical proof-of-generalization in `KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md` §6.

**14. What must happen before autonomous AI execution?** The explicit five-item gate in `KLAROS_SECURITY_EVOLUTION_PLAN.md` §7: plan-validation tests, tenant-isolation tests for every table the agent's allowlist touches, hard-limit tests actually triggering, prompt-injection tests, and a successful staging dry run — no `Agent` may exceed `Recommend` tier in production until all five pass, per `KLAROS_TESTING_STRATEGY.md` §2.

**15. What must happen before production launch (of the full platform)?** RLS Phase C completion for all tables in active use, a working CI pipeline, a proven staging environment, real S3 object storage (not the current genuinely-unimplemented stub), and the full Phase 11 exit criteria in `KLAROS_IMPLEMENTATION_ROADMAP.md`.

**16. What should Phase 1 build?** See "What Phase 1 of the roadmap should build, and why" above.

**17. What to explicitly not build yet?** Full list and reasoning in `KLAROS_DO_NOT_BUILD_YET.md`: no FastAPI/PostgreSQL/Temporal/ToolRegistry replacement, no ungoverned agent framework, no unrestricted SQL for agents, no arbitrary generated production code, no duplicate CRM/finance systems per vertical, no presenting stub integrations as production, no hard-coding around the two test businesses, no chatbot-only redesign.

**18. Top 10 architectural risks** (see also the failure/edge-case appendix below):
1. No DB-level tenant isolation backstop today (RLS absent) — top structural risk, addressed by ADR-004's phased rollout.
2. `autonomy_level` being assumed functional when it is inert — addressed by this document's central finding.
3. No loop/depth/rate protection on tool calls today — must ship before any agent executes.
4. No CI pipeline — regressions currently rely entirely on manual test runs.
5. Object storage S3 path genuinely unimplemented — blocks safe, tenant-isolated storage at scale for Website/Knowledge assets.
6. Website generation, if done wrong (arbitrary code), is an unreviewable multi-tenant attack surface — mitigated by the fixed component-registry decision (ADR-005).
7. Component registry becoming a bottleneck for visual variety — accepted tradeoff, mitigated by shipping enough core components before declaring Phase 6 done.
8. Approval fatigue undermining the human-in-the-loop safety model if tiers/limits are misconfigured — mitigated by tenant-level `AgentPolicy` caps and grouped review screens.
9. Stub integrations being misrepresented to end users as functional — addressed by the `implementation_status` taxonomy and CI drift-check.
10. Backend test suite's pass/fail status not verified (only collection) — must be confirmed before relying on "1,417 tests" as a quality signal, per `KLAROS_TESTING_STRATEGY.md`.

**19. Top 10 reusable existing components:**
1. `ToolRegistry`/`AIExecutionService` — the entire Agent Runtime builds on this unmodified.
2. RBAC (`Role`/`Permission`, `CurrentUser` dependency chain) — extended with new permission values only.
3. `ApprovalRequest`/`ApprovalStatus`/`ApprovalExecutionStatus` — reused as-is for `AgentApproval` and `PublishRequest`.
4. `AuditLog`/`AIInvocationLog` — reused as-is, two additive columns total.
5. `Lead`/`Customer`/`Appointment` — the foundation both verticals extend rather than fork.
6. `CompanyMemory`'s `PENDING→ACTIVE` human-confirmation gate — the pattern reused for Blueprint-fact durability.
7. `IntegrationConnection` + Stripe/QuickBooks/Google Calendar/Twilio clients — untouched, marketplace is a metadata layer on top.
8. Temporal + Automation Engine — new workflow definitions registered alongside the existing five.
9. `public_leads.py`'s signed-token/rate-limited pattern — the template for every new public-facing endpoint (website forms, appointment requests).
10. `ai_provider.py`'s `AI_PROVIDER`/`DeterministicAIProvider` abstraction — every new AI call site (Discovery, Agent planning, Website content) reuses it unchanged, inheriting its honest-degrade behavior.

**20. Exact dependency order for implementation:** Phase 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 (hard-gated by 0) → 8 → {9, 10 in parallel} → 11 (ongoing from Phase 0 onward for its RLS-completion component). Full phase-by-phase dependency detail in `KLAROS_IMPLEMENTATION_ROADMAP.md`.

---

## Appendix: Failure / edge-case analysis

| Scenario | Recovery design |
|---|---|
| User changes business model after Blueprint is `ACTIVE` | New `DiscoverySession` against the same Blueprint produces a new `version`; prior confirmed claims are never silently overwritten — user reviews a diff before re-confirming (`KLAROS_BUSINESS_DISCOVERY_SPEC.md` §8). |
| Incomplete information during Discovery | Blueprint stays `DRAFT`; capability-critical gaps become explicit `Unknown` claims or follow-up questions, never silently defaulted (`KLAROS_BUSINESS_DISCOVERY_SPEC.md` §5). |
| AI misunderstands the business | Human reviews and edits/rejects individual `BlueprintClaim` rows before confirmation — correction is a first-class UI action, not a "regenerate and hope" black box. |
| Recommended integration unavailable (stub) | `implementation_status=STUB` is never hidden; Recommendation text explicitly explains the limitation rather than recommending it as if functional (`KLAROS_INTEGRATION_MARKETPLACE_SPEC.md` §3). |
| OAuth connection fails | Existing `ConnectionStatus=ERROR` path (real today) surfaces `last_error`; unchanged by this plan. |
| Website generation fails validation | `PublishRequest` cannot be created; the specific failed check (schema/broken-link/contrast/etc.) is surfaced to the user (`KLAROS_WEBSITE_BUILDER_SPEC.md` §10). |
| AI agent produces an invalid plan | Deterministic plan validation rejects it outright before any tool executes; execution marked `FAILED` with the validation reason recorded, no partial/silent execution (`KLAROS_AI_AGENT_ARCHITECTURE.md` §6 step 4). |
| Unauthorized tool call attempted | Blocked by `AgentToolPermission` allowlist check *and*, redundantly, by the existing RBAC/policy layer underneath — two independent gates. |
| External API unavailable | Tool call fails normally through the existing error path; `AgentExecutionStep.status=FAILED`, execution halts rather than continuing to a dependent step with stale data. |
| Payment fails | Existing Stripe failure-handling path, unchanged; an `Order`/`Invoice` remains in its existing pending/failed state, no new payment-retry logic introduced by this plan beyond what exists. |
| Duplicate webhook delivery | Existing `WebhookEvent`/`WebhookProcessingStatus` dedup handling, unchanged. |
| Duplicate agent action | New idempotency-key check across steps within one `AgentExecution` (`KLAROS_AI_AGENT_ARCHITECTURE.md` §5), extending the existing idempotency-key pattern to the plan level for the first time. |
| Business changes after launch (website/blueprint drift) | New Blueprint version triggers a "Website may be out of date" flag on the `Site`, surfaced to the user; republishing remains a deliberate human action, never automatic. |
| Tenant deleted | Out of scope for this analysis — existing tenant-deletion behavior (**UNKNOWN — REQUIRES VERIFICATION**, not established in either audit pass) should cascade to all new tables via standard FK-cascade rules matching whatever the existing pattern is for `Organization` deletion. |
| User revokes an integration | Existing `ConnectionStatus=DISCONNECTED` path; any `Recommendation`/`Agent` depending on that capability surfaces a "connection required" state rather than silently failing on next use. |
| Agent loses context (session/conversation gap) | `AgentContext` is reconstructed fresh from persisted `BusinessBlueprint`/`CompanyMemory`/`KnowledgeChunk` state on each new trigger — no reliance on ephemeral in-memory state surviving a restart, since `AgentMemory` is explicitly scoped to one execution only. |
| Knowledge contains malicious instructions (prompt injection via uploaded content) | Existing fenced-DATA-block mitigation pattern (`ai_provider.py`'s `_SYSTEM_INSTRUCTIONS`) extended to every Context Assembly input; dedicated prompt-injection test suite required before `Execute-approved` tier (`KLAROS_SECURITY_EVOLUTION_PLAN.md` §7). |
| User attempts to bypass approval | Not possible via the API layer — `AgentApproval`/`ApprovalRequest` gating happens server-side in `AgentExecutionService`/`ToolRegistry`, never client-enforced; a direct API call attempting to skip it hits the same policy check a normal request would. |
| Cross-tenant data accidentally referenced | RLS backstop (ADR-004) plus the mandatory tenant-isolation test tag for every new table (`KLAROS_TESTING_STRATEGY.md` §1) are the two independent defenses. |
| AI provider unavailable | Existing `DeterministicAIProvider` fallback, unchanged — every new AI call site inherits this automatically via `ai_provider.py` reuse (ADR-010). |
| AI cost becomes excessive | Per-execution spend cap (`AgentPolicy`), `financial_risk`-tagged tools always requiring approval, and `AIInvocationLog.estimated_cost_usd` aggregation available for tenant-level cost dashboards (`KLAROS_TARGET_ARCHITECTURE.md` §10). |

---

NO APPLICATION CODE WAS MODIFIED.
NO DATABASE SCHEMA WAS MODIFIED.
NO DEPENDENCIES WERE CHANGED.
NO ENVIRONMENT VARIABLES WERE CHANGED.
NO DEPLOYMENT CONFIGURATION WAS CHANGED.

Only the 20 requested markdown documents were created in the repo root.
