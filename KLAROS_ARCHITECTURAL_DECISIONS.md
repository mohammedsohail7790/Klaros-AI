# Klaros — Architectural Decision Records

Status: proposal only. No code implements these decisions yet. Each ADR: Context, Problem, Options, Decision, Reasoning, Tradeoffs, Consequences, Migration impact.

---

## ADR-001: Business Blueprint as canonical business representation

**Context**: Klaros needs a single source of truth for "what is this tenant's business" that Discovery writes, Recommendation/Website/Agent read, and a human can review/edit.

**Problem**: Where does this live — embedded fields on `Organization`, a freeform JSON blob, or a first-class versioned entity?

**Options**: (A) Extend `Organization` with more columns. (B) A single JSON blob column somewhere. (C) First-class `BusinessBlueprint`/`BlueprintSection`/`BlueprintClaim` entities (chosen, `KLAROS_BUSINESS_BLUEPRINT_SPEC.md`).

**Decision**: (C). `Organization` remains the tenant/billing root; `BusinessBlueprint` is a versioned 1:1 child.

**Reasoning**: `Organization` already has a clear, narrow responsibility (tenancy, billing, AI kill-switch) — verified 17 columns, none blueprint-shaped (`organization.py`, verified). A JSON blob loses queryability (Recommendation matching needs to query `REQUIRED_CAPABILITIES` directly) and versioning (editing after `ACTIVE` needs history, per §6 of the spec). First-class entities with a `BlueprintClaim` fact-table give per-fact confidence/evidence/source tracking the brief explicitly requires (Facts/Inferences/Assumptions/etc.).

**Tradeoffs**: more tables to maintain than a blob; `BlueprintSection.data` is still JSONB (not fully normalized) to avoid a migration per vertical nuance — an intentional partial-normalization compromise, not full relational purity.

**Consequences**: every new consumer (Recommendation, Website, Agent) has one stable place to read from; vertical extensions register sub-schemas without core migrations.

**Migration impact**: net-new tables only, zero change to `Organization`.

---

## ADR-002: Agent runtime architecture

**Context**: no agent framework exists; `AIExecutionService` is an intentionally narrow, already-correct boundary (verified, `execution_service.py:39-54`).

**Problem**: build a new orchestration engine, adopt a third-party agent framework (LangChain/etc.), or build a thin `Agent`/`AgentExecution` layer on top of the existing boundary.

**Options**: (A) Third-party agent framework. (B) New bespoke orchestration engine independent of `ToolRegistry`. (C) Thin new entity model + `AgentExecutionService` calling the existing, unchanged `AIExecutionService`/`ToolRegistry` per step (chosen).

**Decision**: (C).

**Reasoning**: the hard, safety-critical part (tenant/permission/policy/approval/audit gating, identical for human and AI) already works and is proven (`registry.py:126-224`). A third-party framework would need to either bypass this gate (unacceptable) or be adapted to call through it per tool invocation, at which point it contributes little over a bespoke thin layer while adding a large new dependency surface and its own security model to audit. See `KLAROS_DO_NOT_BUILD_YET.md` §4–§5.

**Tradeoffs**: building the planning/orchestration loop ourselves means no community ecosystem of prebuilt agent patterns — accepted, since the safety requirements here (tenant isolation, financial hard limits) are domain-specific enough that a generic framework wouldn't provide them anyway.

**Consequences**: `AgentExecutionService` is new code Klaros fully owns and must maintain; `ToolRegistry`/`AIExecutionService` need zero logic changes, only additive FK columns for correlation.

**Migration impact**: net-new tables/services; two additive nullable columns on existing tables (`KLAROS_DATABASE_EVOLUTION_PLAN.md` §3).

---

## ADR-003: Governed tool execution

**Context**: 57+ tools already exist behind `ToolRegistry`; the naive "give the agent all of them" approach is a real risk (unauthorized/hallucinated tool calls, cross-tenant access).

**Problem**: how does an agent's tool access get scoped?

**Options**: (A) Agent gets the full tool list, relies on prompt instructions to self-limit. (B) Agent gets a per-agent allowlist enforced deterministically before execution (chosen, `AgentToolPermission`). (C) Agent gets role-based access identical to a human user of the same RBAC role, no additional narrowing.

**Decision**: (B), layered *underneath* (C) — an agent's effective access is the intersection of its `AgentToolPermission` allowlist and its backing RBAC role's permissions, never wider than either alone.

**Reasoning**: prompt-based self-limiting (A) is not a security control — it degrades under prompt injection and model error. RBAC-only (C) is too coarse: a role permitting a human to do many things doesn't mean every agent acting "as" that role should be able to do all of them autonomously — an allowlist lets a tenant scope a specific agent tightly (e.g. a scheduling agent that can only call `crm.check_availability`/`crm.create_appointment`, not `finance.create_refund`, even if the backing role technically has refund permission).

**Tradeoffs**: more configuration surface per agent (a new allowlist to manage) — mitigated by sensible defaults (`AgentPolicy` tenant-level defaults) and templates for common agent types.

**Consequences**: plan validation (`KLAROS_AI_AGENT_ARCHITECTURE.md` §6 step 4) is a hard, deterministic gate, not a suggestion.

**Migration impact**: new `AgentToolPermission` table; zero change to `ToolRegistry`'s own permission logic.

---

## ADR-004: Tenant isolation strategy

**Context**: verified app-layer-only isolation, no RLS anywhere, flagged as the top structural risk in the original audit (P1).

**Problem**: how to add a DB-level backstop without breaking the live, 208-table, ~70-service application.

**Options**: (A) Big-bang RLS enablement across all tables. (B) RLS only on new tables, existing tables left as-is indefinitely. (C) Phased rollout: instrument → permissive-with-bypass → remove bypass by sensitivity order, new tables RLS-on from day one (chosen, `KLAROS_SECURITY_EVOLUTION_PLAN.md` §2).

**Decision**: (C).

**Reasoning**: (A) risks an outage from an unanticipated code path (e.g. background jobs) that doesn't set tenant context correctly — too risky against a live application with real tenant data and no verified full-coverage precedent for this pattern in the codebase. (B) leaves the actual highest-risk data (existing financial/CRM tables) permanently unprotected, failing to address the audit's finding at all. (C) bounds the blast radius to new, zero-traffic tables first while proving the mechanism, then extends deliberately.

**Tradeoffs**: slower to fully close the gap than (A); requires sustained, multi-phase engineering discipline rather than a single migration.

**Consequences**: existing tables remain app-layer-only for a transition period — an explicitly accepted, time-bounded risk, not a permanent one.

**Migration impact**: no schema change for RLS enablement itself (policies, not columns); `SET LOCAL app.current_tenant_id` addition to the request dependency chain is a small, additive code change.

---

## ADR-005: Website generation architecture

**Context**: brief explicitly rejects prompt→LLM→arbitrary-code→production; nothing exists today to build on.

**Problem**: how much of the generation pipeline should be AI-authored code vs. a bounded specification.

**Options**: (A) LLM generates and deploys arbitrary React/HTML/JS per tenant. (B) Pure JSON/DSL with no visual component library — maximal safety, minimal visual quality. (C) Fixed, reviewed component registry + JSON specification, LLM fills only declared content/text slots (chosen, `KLAROS_WEBSITE_BUILDER_SPEC.md`).

**Decision**: (C).

**Reasoning**: (A) is an unreviewable, per-tenant attack surface at multi-tenant SaaS scale — rejected outright, matching `KLAROS_DO_NOT_BUILD_YET.md` §7. (B) is safe but produces visually poor results, undermining the product goal of "real websites," and is more work than (C) for less payoff. (C) gets DSL-level safety (nothing executes that wasn't engineer-reviewed) with hand-built-quality visuals, at the accepted cost that new component types require an engineering change rather than an AI/tenant action.

**Tradeoffs**: slower iteration on new visual patterns than a fully generative approach; component registry becomes a product surface Klaros must actively maintain.

**Consequences**: Preview and Production render through the identical registry — no "it worked in preview but broke in prod" class of bug from a code-generation mismatch, because there is no per-tenant code to mismatch.

**Migration impact**: entirely net-new subsystem, new deploy unit (`website-runtime`), zero impact on existing app.

---

## ADR-006: Integration marketplace architecture

**Context**: `IntegrationConnection` (per-tenant connection state) is real and correct for its purpose; no catalog/metadata/recommendation layer exists; verified stub providers (Xero, Google Ads, etc.) have no data-level distinction from real ones today.

**Problem**: extend `IntegrationConnection`'s schema to add catalog fields, or add a separate catalog table.

**Options**: (A) Add `capabilities`/`implementation_status`/etc. columns directly to `IntegrationConnection`. (B) Separate `IntegrationProviderCatalog` table, tenant-independent, joined at query time (chosen).

**Decision**: (B).

**Reasoning**: `IntegrationConnection` is a per-tenant row (one per `(tenant_id, provider)`); catalog metadata (is Xero a stub, what capabilities does Stripe have) is tenant-independent and would be needlessly duplicated across every tenant's connection rows under (A), and would require a connection row to exist before a provider could even be *listed* as available — backwards, since the marketplace must show `RECOMMENDED`/`NOT_CONNECTED` providers a tenant has never touched.

**Tradeoffs**: one more join at query time — negligible cost, matches the existing codebase's normalized-table conventions elsewhere.

**Consequences**: catalog can be updated (e.g. flipping a stub to real) via a data migration without touching any tenant's connection data.

**Migration impact**: new table only; zero change to `IntegrationConnection`.

---

## ADR-007: Business-domain extensibility

**Context**: brief requires supporting Medical Tourism and Dropshipping (and future verticals) without hard-coding around them.

**Problem**: how do vertical-specific concepts (providers, SKUs) plug in without special-casing core services.

**Options**: (A) `if vertical == X` branches inside core services (`LeadService`, etc.). (B) Fully separate parallel CRM/Finance per vertical. (C) Additive extension tables referencing core entities by FK, a registry pattern, zero core-service awareness of specific verticals (chosen, `KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md`).

**Decision**: (C).

**Reasoning**: (A) makes core services grow unboundedly with each new vertical and violates the brief's explicit "do not hard-code" instruction. (B) fragments RBAC/audit/reporting and duplicates already-correct, generic logic (`Lead`/`Customer` are verified domain-agnostic). (C) keeps core services vertical-blind while still allowing genuinely new concepts (hospitals, SKUs) their own tables.

**Tradeoffs**: requires discipline to keep extension code from creeping into core files during implementation — mitigated by the one-directional dependency rule (extensions import core, never the reverse) being enforced via code review / module-boundary lint in Phase 1.

**Consequences**: a third vertical is addable with zero core-service changes, per the "Home Renovation Financing" proof-of-generalization example in the domain extensibility spec.

**Migration impact**: net-new tables per vertical; two additive columns on `ReferralReward` for Medical Tourism's commission currency.

---

## ADR-008: Workflow generation architecture

**Context**: Automation Engine (deterministic trigger→condition→action) is real; no AI-to-workflow compiler exists; arbitrary AI-generated executable code is rejected per `KLAROS_DO_NOT_BUILD_YET.md` §7.

**Problem**: how does an AI-proposed workflow become something that actually runs.

**Options**: (A) AI generates and executes arbitrary code/scripts per workflow step. (B) AI proposes a workflow *specification* drawn from the existing `Automation`/`AutomationCondition`/action vocabulary, a human approves, the system materializes it as a normal `Automation` row (chosen).

**Decision**: (B).

**Reasoning**: identical reasoning to ADR-005 — a bounded vocabulary (existing trigger types, existing tool-backed actions) is reviewable and safe; arbitrary code is not. The Automation Engine already has exactly the right shape (deterministic trigger→condition→action) to be the execution target.

**Tradeoffs**: an AI-proposed workflow can only use actions the vocabulary already supports — if a genuinely new action type is needed, that's a new Tool (via the existing `ToolRegistry` registration process), not something the AI can invent on the fly.

**Consequences**: workflow generation is a thin proposal-and-materialization layer, not a new execution engine.

**Migration impact**: no schema change to `Automation`/`AutomationVersion`; new proposal/review endpoints only.

---

## ADR-009: Knowledge vs. Memory architecture

**Context**: `KnowledgeFile`/`KnowledgeChunk` (RAG) and `CompanyMemory` (structured facts) both already exist and are real, but distinct, and the brief asks for a clearer target taxonomy including Blueprint-derived facts and per-execution agent state.

**Problem**: where does a new kind of business context (Blueprint facts, agent working memory) live — a new table, or one of the two existing systems.

**Options**: (A) Everything new gets shoved into `CompanyMemory` regardless of shape. (B) A new, six-way taxonomy (Facts/Knowledge/Memory/State/Conversation Context/Agent Working Memory) with clear ownership per `KLAROS_TARGET_ARCHITECTURE.md` §7 (chosen).

**Decision**: (B), with the rule that anything promoted to durable, cross-run authority flows through `CompanyMemory`'s existing `PENDING→ACTIVE` human-confirmation gate — never a new, weaker confirmation path.

**Reasoning**: `CompanyMemory`'s value is precisely its human-confirmation discipline (verified: AI-proposed memories never reach `ACTIVE`/context-visible status without `confirm_memory`); reusing that gate for Blueprint-fact mirroring and any agent-learned durable fact preserves that discipline rather than inventing a parallel, potentially laxer one. Per-execution `AgentMemory` is explicitly *not* durable and must never be treated as such.

**Tradeoffs**: an extra hop (Blueprint claim confirmed → mirrored into `CompanyMemory`) rather than agents reading Blueprint directly — accepted, since it means every existing/future AI call site that already consumes `CompanyMemory` (Morning Brief, eventually the voice receptionist per the audit's noted gap) gets Blueprint facts "for free" without learning a new API.

**Consequences**: closes, rather than widens, the audit's noted gap that the voice receptionist doesn't consume `CompanyMemory` — future work to wire that up now also picks up Blueprint facts automatically.

**Migration impact**: none to existing `CompanyMemory`/`KnowledgeFile` schema; new tables for Blueprint/Discovery/Agent-scoped concepts only.

---

## ADR-010: AI model/provider abstraction

**Context**: `AI_PROVIDER` (Anthropic/OpenAI switch) with `DeterministicAIProvider` fallback already exists and is real (`ai_provider.py`, verified).

**Problem**: does the Agent Runtime need a new/different model-routing layer.

**Options**: (A) New, separate model-router abstraction for agent planning calls. (B) Reuse the existing `AI_PROVIDER` abstraction for all new AI call sites (Discovery extraction, agent planning, website content generation), extending it only if a genuine new requirement emerges (e.g. per-agent model override) (chosen).

**Decision**: (B).

**Reasoning**: the existing abstraction already does the two things that matter — provider selection and an honest deterministic fallback — and every new AI call site in this plan (Discovery, Agent planning, Website content) has the same shape (a prompt in, structured output out, safe to degrade deterministically). Introducing a second abstraction for "agent calls" specifically would fragment provider-key management and the fallback behavior for no demonstrated benefit.

**Tradeoffs**: per-agent model override (`AgentVersion.model_provider`/`model_name`, `KLAROS_AI_AGENT_ARCHITECTURE.md` §2) is a thin parameter passed into the existing abstraction, not a new routing system — sufficient for the stated requirements, revisit only if genuine multi-provider-per-call-in-one-plan routing becomes a real need (not currently justified).

**Consequences**: deterministic-fallback behavior (AI features effectively off without a configured API key) is inherited automatically by every new AI-touching feature, preserving the existing "honest degrade, never silent mock" principle across the whole platform.

**Migration impact**: none to `ai_provider.py`'s existing logic; `AgentVersion` carries provider/model as configuration, read by the same abstraction.

## Cross-references

Every ADR above is referenced from its corresponding spec document; see `KLAROS_TARGET_ARCHITECTURE.md` and `KLAROS_DO_NOT_BUILD_YET.md` for the broader reasoning these decisions draw on.
