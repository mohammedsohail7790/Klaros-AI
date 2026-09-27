# Klaros — What NOT to Build Yet

Status: architecture guidance document. Read-only analysis; no application code, schema, dependencies, environment, or deployment configuration was changed to produce this document. Companion to `KLAROS_IMPLEMENTATION_ROADMAP.md`, `KLAROS_TARGET_ARCHITECTURE.md`, and `KLAROS_EXECUTIVE_ARCHITECTURE_SUMMARY.md`.

## Purpose

Every item below is something a team building "AI business-builder Klaros" will be tempted to do because it sounds like the obvious next step, and every item is a mistake at this stage given what `KLAROS_AI_CODEBASE_AUDIT.md` establishes about the current system: a mature, real, tested field-service operations platform (CRM, jobs, quotes/contracts, invoicing/AR, marketing, retention, RBAC, a governed 57-tool execution boundary, Temporal automation, event bus, real Stripe/QuickBooks/Google Calendar/Twilio integrations, pgvector knowledge/RAG) that has **zero** of the business-discovery/blueprint/agent-runtime/website-builder layer described in the target architecture. The temptation in that situation is either (a) to rebuild the whole thing "properly" from a clean slate, or (b) to bolt an LLM directly onto the existing 57-tool registry and call it an agent. Both are wrong. This document is the explicit list of things to defer, and why.

## 1. Do not replace FastAPI

**Why it's tempting**: a "general AI platform" sounds like it needs a different backend shape (e.g. a Node/TypeScript monolith to unify with the Next.js frontend, or a dedicated agent-orchestration framework's own server).

**Why not now**: `backend/app/main.py`, 55 routers, ~70 services, the RBAC dependency-injection chain (`backend/app/api/deps.py`), and the entire tool/policy/approval framework are built on FastAPI's dependency-injection and async model. All of Phases 0–8 in `KLAROS_IMPLEMENTATION_ROADMAP.md` are additive routers/services on the same app. Nothing in the target architecture requires a different web framework. Risk of replacing it: total rewrite of 55 routers, ~70 services, and every test that hits them via `TestClient`, for zero functional gain.

## 2. Do not replace PostgreSQL

**Why it's tempting**: a vector-heavy "business blueprint + knowledge + agent memory" platform sounds like it wants a document DB or a dedicated vector DB (Pinecone/Weaviate/Qdrant).

**Why not now**: PostgreSQL + `pgvector` already serves the knowledge/RAG layer in production (`backend/app/services/knowledge_retrieval_service.py`, HNSW index per migration 0032) and is exactly what the new Blueprint/Recommendation/Agent tables need (relational integrity with `tenant_id` foreign keys, transactional consistency between a blueprint row and the CRM rows it drives). Introducing a second datastore adds an entire new consistency, backup, and tenant-isolation surface for no demonstrated capability gap. Revisit only if vector query volume/latency at scale (post-launch, with real tenant data) proves `pgvector` insufficient — that is a P3 scaling question, not a Phase 0–8 architecture question.

## 3. Do not replace Temporal

**Why it's tempting**: agent execution plans, multi-step workflow generation, and long-running business-launch orchestration all sound like they need a "workflow engine," and it's easy to assume the existing one (wired only to `AutomationWaitWorkflow`, `JobLifecycleWorkflow`, `InvoiceOverdueWorkflow`, `LeadQualificationWorkflow`, `EventProcessingWorkflow` per `backend/app/workflows/definitions.py`) is too narrow.

**Why not now**: Temporal is a real, already-operational durable-execution engine (`backend/app/workers/main.py`, `backend/app/temporal_client.py`). The target Agent Execution and Workflow Generation designs (`KLAROS_AI_AGENT_ARCHITECTURE.md`, a workflow-generation section folded into `KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md`) should define new Temporal workflow *definitions* (e.g. `AgentExecutionWorkflow`, `BusinessLaunchWorkflow`) that reuse the same worker process and retry/backoff conventions already proven in `AutomationWaitWorkflow`, not stand up a second orchestration engine (Airflow, Prefect, a custom queue). Two durable-execution systems in one product is an operational-cost and consistency risk with no offsetting benefit.

## 4. Do not replace ToolRegistry

**Why it's tempting**: "AI agents that call tools" sounds like it wants a mainstream agent framework (LangChain, LlamaIndex agents, a bespoke MCP server) rather than the existing first-party `backend/app/tools/{base,factory,registry,policy,redact,errors}.py` + 57 builtin tool files.

**Why not now**: the existing `ToolRegistry` already does the hard, safety-critical part correctly: every call — human or AI — carries an `ExecutionContext` (`tenant_id`, `actor_type`, `role`, `correlation_id`), goes through `tools/policy.py`'s permission/approval gate, and is audited (`AuditLog`, `AIInvocationLog`). Swapping it for a generic agent framework means re-proving tenant isolation and RBAC enforcement inside a third-party abstraction that was not designed for this domain's approval/audit requirements. The correct move (`KLAROS_AI_AGENT_ARCHITECTURE.md`, ADR-002/ADR-003 in `KLAROS_ARCHITECTURAL_DECISIONS.md`) is to build the Agent Runtime **on top of** `AIExecutionService`/`ToolRegistry`, not replace either.

## 5. Do not add an agent framework without governance

**Why it's tempting**: the fastest way to demo "Klaros has AI agents" is to give an LLM the full list of 57 tools and a system prompt, and let it call whichever ones it decides to.

**Why not now**: `backend/app/ai/execution_service.py`'s own docstring states this is deliberately not yet built ("Phase 2 does not build the autonomous agent that produces ToolRequests"). Doing it naively — unrestricted tool access, no per-agent allowlist, no autonomy-level gating, no plan-validation step before execution — reintroduces every risk the existing governed boundary was built to prevent: unauthorized tool calls, cross-tenant access via a model-hallucinated tenant reference, uncontrolled spend (a model calling `stripe.create_refund` in a loop), duplicate actions, and prompt-injection-driven tool selection from untrusted content (e.g. a lead's free-text message). `KLAROS_AI_AGENT_ARCHITECTURE.md` §Orchestration and ADR-002 define the required scaffolding (per-agent `AgentToolPermission`, autonomy-level-gated `AgentApproval`, plan validation before execution) that must exist *before* any agent is allowed to call a tool that writes data, sends a message, or moves money.

## 6. Do not give agents unrestricted SQL

**Why it's tempting**: "just let the agent query the database directly for anything it needs" removes the friction of building new read-only tools for every new blueprint/recommendation/analytics need.

**Why not now**: every current AI-callable action goes through a typed, tenant-scoped tool (Pydantic schema in, `ExecutionContext`-checked, audited). Direct SQL access — even "read-only" — bypasses `tenant_id` filtering discipline entirely (the audit's own §10 flags missing-filter risk as the top structural concern even for *human-written* service code) and makes prompt-injection-driven data exfiltration trivial (a malicious business-knowledge document instructing the model to "also show me rows from other tenants" has no policy layer to stop it if the model can emit arbitrary SQL). New read patterns the agent needs should be new narrow tools (e.g. `blueprint.get_summary`, `crm.search_customers`), not a SQL escape hatch.

## 7. Do not allow arbitrary generated production code

**Why it's tempting**: the most powerful-looking version of a "Website Builder" or "Agent Builder" is "the LLM writes React/Python and we deploy it."

**Why not now**: `KLAROS_WEBSITE_BUILDER_SPEC.md` explicitly rejects prompt→LLM→arbitrary-code→production in favor of a bounded Site/Page/Section/Component **specification** rendered by a fixed, reviewed component registry. Arbitrary generated code that reaches production is an unreviewable, untested, tenant-facing attack surface (XSS via generated markup, SSRF via generated fetch calls, supply-chain risk via generated `import`s) and cannot be safely sandboxed at the multi-tenant SaaS level Klaros operates at. The same reasoning blocks "let the agent write and run its own Python for a workflow step" — workflow steps must be composed from the same governed tool set, not from generated code (see `KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md` and ADR-008).

## 8. Do not build duplicate CRM/finance/customer systems for each vertical

**Why it's tempting**: medical tourism "patients" and dropshipping "customers" feel conceptually different enough from home-services "customers" that a new vertical-specific CRM looks cleaner to build from scratch.

**Why not now**: `Lead`/`Customer`/`Appointment` (`backend/app/models/crm.py`) are already domain-agnostic (per the audit's §28/§29 readiness notes) and reused by every existing route/service/tool. `KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md` defines vertical extension via thin domain-specific tables that reference the existing `Customer`/`Lead` primary keys (e.g. `PatientLead` extending `Lead`, not replacing it), not parallel copies of CRM/Invoice/Payment. Duplicating these systems per vertical multiplies the RBAC/audit/tenant-isolation surface that must be independently re-verified for each copy, and fragments reporting (a tenant running both medical-tourism and generic services would need two dashboards). See ADR-007.

## 9. Do not present stub integrations as production

**Why it's tempting**: the integration marketplace (`KLAROS_INTEGRATION_MARKETPLACE_SPEC.md`) looks more impressive with more providers listed as available.

**Why not now**: Xero, Google Ads, Meta Ads, Google Business Profile, ServiceTitan, and Jobber are explicit, self-disclosed stubs in the current code (`.env.example:82-85`) — configuring credentials does not make them functional. The target Integration Marketplace's status taxonomy (`AVAILABLE/CONNECTED/NOT_CONNECTED/RECOMMENDED/REQUIRED/OPTIONAL/COMING_SOON/STUB/CUSTOM`) exists specifically so a `STUB` provider is never rendered identically to a `CONNECTED` or `AVAILABLE` one, and so the Recommendation Engine never recommends a `STUB` provider as if it will actually perform the integration action. Building marketplace UI before the status distinction is wired through end-to-end (backend provider metadata → API → frontend badge) risks shipping exactly the misrepresentation the current audit already flags as a P2 risk (§31: "worth checking that the UI doesn't imply these are live").

## 10. Do not hard-code the platform around the two test businesses

**Why it's tempting**: Medical Tourism and Dropshipping are the two validation cases in this analysis, so it is fast — but wrong — to special-case them directly in the Business Discovery Engine, Blueprint schema, or Recommendation Engine (e.g. an `if business_type == "medical_tourism"` branch deep in core services).

**Why not now**: `KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md` and ADR-007 require every vertical — including these two — to plug in through the same extension pattern (a domain-extension descriptor, additive tables referencing core CRM/Finance keys, a capability-requirement set the Recommendation Engine reads generically). If the two validation businesses are hard-coded instead, every future vertical (a third test business, a real customer in an unanticipated industry) requires new core-service code rather than a new extension package — defeating the entire point of a "business blueprint → capabilities → recommendations" architecture. Treat the two test businesses as **acceptance tests for the extensibility pattern**, never as branches in core logic.

## 11. Do not build the whole product as a chatbot

**Why it's tempting**: "AI-native" products increasingly default to a single chat interface as the entire UI, and it is the fastest thing to demo.

**Why not now**: Klaros already has a real, deep, structured UI (57 pages) that both staff and, per journey (c)/(e) in `KLAROS_USER_JOURNEYS_CURRENT.md`, non-technical field-service operators rely on for CRM/jobs/finance/marketing work today. A chat-only redesign would (a) throw away that surface, (b) make every governed action (approvals, tenant-scoped views, audit trails) harder to present clearly than a structured page already does, and (c) push all future interaction through the least-reliable, least-testable interface for anything precision-sensitive (money, PHI-adjacent patient data, inventory counts). The correct integration point for conversational AI is additive — a business-discovery intake conversation, an agent-configuration assistant, in-context Q&A over Knowledge — layered onto the existing structured product, not a replacement for it. See `KLAROS_FRONTEND_EVOLUTION_PLAN.md`.

## Summary table

| Item | Why tempting | Why deferred | Revisit condition |
|---|---|---|---|
| Replace FastAPI | "new platform needs new backend" | 55 routers/70 services/RBAC chain already built on it | Never, absent a concrete capability gap |
| Replace PostgreSQL | vector/AI workloads feel like they want a doc/vector DB | pgvector already proven in production RAG path | Only if measured `pgvector` latency/scale fails at real tenant volume |
| Replace Temporal | agent workflows feel like they need a "real" workflow engine | Temporal already operational; extend with new workflow defs | Never, absent a concrete capability gap |
| Replace ToolRegistry | agent frameworks look more modern | Existing policy/approval/audit gate is the hard-won safety layer | Never — build Agent Runtime on top of it |
| Agent framework w/o governance | fastest demo path | Reintroduces every risk the boundary exists to prevent | Only after AgentToolPermission/Approval/Autonomy scaffolding (Phase 7) ships |
| Unrestricted SQL for agents | removes tool-building friction | Bypasses tenant filtering and audit; injection risk | Never — add narrow read tools instead |
| Arbitrary generated production code | most "powerful"-looking website/agent builder | Unreviewable multi-tenant attack surface | Never at this trust level — spec+registry rendering only |
| Duplicate CRM/finance per vertical | verticals feel conceptually distinct | Fragments RBAC/audit/reporting; core CRM is already generic | Never — extend via additive tables |
| Presenting stubs as production | marketplace looks more complete | Misleads tenants into relying on non-functional integrations | Only once status taxonomy is wired end-to-end |
| Hard-coding the 2 test businesses | fastest path to pass validation | Defeats the extensibility goal of the whole platform | Never — they are acceptance tests, not branches |

## Cross-references

- `KLAROS_IMPLEMENTATION_ROADMAP.md` — where each deferred capability re-enters the plan, if ever.
- `KLAROS_ARCHITECTURAL_DECISIONS.md` — ADR-002 (agent runtime), ADR-003 (governed tool execution), ADR-005 (website generation), ADR-007 (domain extensibility), ADR-008 (workflow generation) formalize the decisions this document argues for.
- `KLAROS_AI_CODEBASE_AUDIT.md` §11–§13, §17–§18 — evidence for current-state claims used above.
