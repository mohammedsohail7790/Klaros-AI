# Klaros — Target Architecture

Status: architecture proposal only; nothing in this document was implemented against the running application. Baseline evidence: `KLAROS_AI_CODEBASE_AUDIT.md` and companions, plus verified source reads cited inline. See `KLAROS_GAP_ANALYSIS.md` for current↔target deltas and `KLAROS_IMPLEMENTATION_ROADMAP.md` for sequencing.

## 1. Target product model — real software boundaries

The audit brief's conceptual pipeline (business discovery → requirements → recommendations → blueprint → website/integrations/workflows → agent runtime → governed tools → business operations) maps to these concrete services, each additive to the existing FastAPI app:

```
Business Discovery Service  →  Requirements Engine  →  Business Blueprint Service
        (new)                      (new)                      (new)
                                                                    │
                                              ┌─────────────────────┼─────────────────────┐
                                              ▼                     ▼                     ▼
                                  Recommendation Engine   Website Builder Service   Agent Runtime
                                        (new)                    (new)                 (new)
                                              │                     │                     │
                                              ▼                     │                     ▼
                                  Integration Marketplace ──────────┘          AIExecutionService (EXISTING,
                                        (new metadata                                 unchanged)
                                     layer over existing                                   │
                                    IntegrationConnection)                                  ▼
                                                                                    ToolRegistry (EXISTING,
                                                                                    unchanged — 57+ tools,
                                                                                    policy/approval/audit gate)
                                                                                             │
                                                                                             ▼
                                                                            Business Operations (EXISTING:
                                                                        CRM/Jobs/Finance/Marketing/Retention)
```

Each "(new)" box is a normal FastAPI router + service + SQLAlchemy models, following the exact same pattern as the ~70 existing services — no new architectural paradigm, no new web framework, no new orchestration engine (see `KLAROS_DO_NOT_BUILD_YET.md`).

## 2. General Agent Runtime

### 2.1 Why it must sit on top of the existing boundary, not beside it

`backend/app/ai/execution_service.py:39-54` (verified) already does the one thing that must never be reimplemented: it builds an `ExecutionContext(actor_type=ActorType.AI, ...)` and calls `ToolRegistry.execute`, which runs the full permission/tenant/kill-switch/policy/approval/audit pipeline (`registry.py:126-224`, verified) identically for AI and human callers. An Agent Runtime's only job is to decide *which* tool to call with *what input*, and to call `AIExecutionService.request_tool_execution` (or a thin wrapper of it) once per step — never to touch the DB, an integration client, or a tool's internals directly.

### 2.2 Entity model (new tables, additive)

- **`Agent`** — `id`, `tenant_id`, `name`, `goal` (text), `instructions` (text, system-prompt-equivalent), `status` (DRAFT/ACTIVE/DISABLED), `created_by`.
- **`AgentVersion`** — `agent_id`, `version`, `instructions`, `model_provider`, `model_name`, `published_at` — every edit to an active agent creates a new version; execution always pins to one version (auditability: "what config produced this action").
- **`AgentGoal`** — optional structured goal decomposition (`objective`, `success_criteria`) when a single free-text `goal` isn't enough for plan validation.
- **`AgentContext`** — what an execution run is allowed to read: a reference to a `BusinessBlueprint`, a set of `KnowledgeFile`/`CompanyMemory` scopes, and a conversation/task input. Never raw DB access.
- **`AgentToolPermission`** — `agent_id`, `tool_name`, `max_calls_per_execution` — the **per-agent allowlist**. An agent with no row for a tool cannot call it, full stop, regardless of what the RBAC role backing it would otherwise permit. This is the control that prevents "give the model all 57 tools."
- **`AgentWorkflow`** — links an `Agent` to an `Automation`/Temporal workflow it can trigger (not execute directly — see §5).
- **`AgentExecution`** — one row per invocation: `agent_id`, `agent_version_id`, `tenant_id`, `triggered_by` (user/event/schedule), `status` (PLANNING/AWAITING_APPROVAL/EXECUTING/SUCCEEDED/FAILED/CANCELLED), `plan` (JSON — the validated step list, see §4), `correlation_id` (reused across every `AuditLog`/`AIInvocationLog`/`ApprovalRequest` row the execution produces).
- **`AgentExecutionStep`** — `execution_id`, `step_number`, `tool_name`, `input`, `tool_output`, `status`, `started_at`, `completed_at` — one row per `AIExecutionService.request_tool_execution` call, giving a full "why did Klaros do this" trace (feeds `KLAROS_AI_AGENT_ARCHITECTURE.md` §Observability).
- **`AgentApproval`** — thin wrapper around the existing `ApprovalRequest` (reuse `ApprovalStatus`/`ApprovalExecutionStatus` as-is, verified shape in `approval.py:44-73` already fits) adding `agent_execution_id` and `step_number` for traceability back to the plan.
- **`AgentPolicy`** — tenant-level defaults for new agents (default autonomy tier, default max steps, default per-execution spend cap) — the tenant-configuration counterpart to §3's per-agent enforcement.

### 2.3 What is explicitly reused unchanged

`ToolRegistry`, `AIExecutionService`, `ActionPolicy`/`TenantToolPolicy`, `ApprovalRequest`/`ApprovalStatus`/`ApprovalExecutionStatus`, `AuditLog`, `AIInvocationLog`, `ActorType`, `EventBus`. None of these require a schema or logic change to support the Agent Runtime — only new FK columns for correlation (e.g. `AIInvocationLog.agent_execution_id`, additive nullable column).

## 3. Agent autonomy system (reconciled with verified current state)

**Verified current state**: `Organization.autonomy_level` (`LEVEL_0`..`LEVEL_4`) is stored but read by zero executing code (`organization.py:60-67`, its own comment: "remains unenforced/decorative"). The only real, enforced gates today are per-tool `ActionPolicy` (`AUTO`/`APPROVAL_REQUIRED`/`BLOCKED`, tool-name-keyed, `SYSTEM_BLOCKED_TOOLS` as an unconditional floor) and the binary `ai_paused` kill switch. This is a materially different (and coarser) reality than the brief's proposed 0–4 scale, and the target design must not silently assume the existing enum values already mean what their doc-comments say.

**Target design** — two independent, composable layers, not a replacement of either:

1. **Tool-level policy (existing, unchanged)**: still the floor. No agent, regardless of its own settings, can execute a `BLOCKED` tool or bypass `SYSTEM_BLOCKED_TOOLS`.
2. **Agent-level autonomy (new)**: a real, enforced per-`Agent` (not per-`Organization`) tier, stored on `Agent`/`AgentPolicy` and **actually read** by the new `AgentExecutionService` before each step:
   - **Observe** — agent may read (via read-only tools) and produce a plan, but every step requires human approval before execution, regardless of the underlying tool's own `ActionPolicy`.
   - **Recommend** — agent may execute tools whose `ActionPolicy` is `AUTO` *and* are tagged low-risk (new per-tool `risk_tier` field, additive to `DEFAULT_TOOL_POLICIES`); anything else routes to `AgentApproval`.
   - **Execute-approved** — agent may execute any tool not `BLOCKED`, but every execution still creates an `AgentApproval` gate for tools already marked `APPROVAL_REQUIRED` by the existing tool policy (i.e. the agent inherits the tool's own gate, nothing looser).
   - **Execute-autonomous** — agent may execute `AUTO` and `APPROVAL_REQUIRED` tools without a human step, **except** financial-risk and communication-risk actions (defined below), which always require approval regardless of tier — a hard ceiling, not a configurable one.
3. **Hard limits (new, enforced in `AgentExecutionService`, independent of tier)**:
   - **Financial**: any tool tagged `financial_risk=True` (refunds, write-offs, payouts, Stripe charges beyond a configured per-execution cap) always requires `AgentApproval`, at every autonomy tier.
   - **Communication**: any tool that sends an external message (SMS/email/customer-facing) is rate-limited per `(agent_id, recipient)` per day and deduplicated by content-hash to block runaway/duplicate messaging.
   - **Integration**: per-integration call-volume caps per execution (reusing the billing/usage-check pattern already proven for `insights.generate_morning_brief`, `registry.py:169-178`).
   - **Execution**: max steps per `AgentExecution` (default, tenant-configurable via `AgentPolicy`), wall-clock timeout, and idempotency-key reuse check across steps in the same plan (new — see §1.4 of `KLAROS_GAP_ANALYSIS.md`, nothing like this exists today).
4. **Tenant policy**: `AgentPolicy` lets a tenant set defaults (e.g. cap every new agent at Recommend tier) and per-agent overrides require an `MANAGE_AGENTS`-permissioned human action, audited like any `TenantToolPolicy` change.

This reconciles the brief's ask ("high-risk/low-risk/approval-required/prohibited actions, financial/communication/integration/rate limits, tenant-specific policy") without pretending the existing `autonomy_level` field already does any of it.

## 4. AI Orchestration

```
User Intent (chat/form/event) → Context Assembly (Blueprint + Knowledge + CompanyMemory,
   tenant-scoped reads only) → Goal (from Agent config or user input) → Plan (LLM-proposed,
   schema-constrained to the agent's AgentToolPermission allowlist) → Plan Validation
   (deterministic: every step's tool_name ∈ allowlist, inputs match the tool's own Pydantic
   schema, no step exceeds hard limits) → Approval if required (§3) → Agent Execution
   (AgentExecutionService, one AIExecutionService.request_tool_execution call per step) →
   Tool Calls (ToolRegistry, existing) → External Integrations (existing clients) →
   Results → State Update (existing service/DB writes, unchanged) → Verification
   (deterministic post-condition check per step type, e.g. "does the created appointment's
   slot match what crm.check_availability returned" — same pattern as the voice receptionist's
   existing _SlotGuard) → Audit (AuditLog + AIInvocationLog + AgentExecutionStep, all existing
   or additive-only schema)
```

**Planning model — hybrid, not purely model-generated**: the *plan* (which tools, in what order) is LLM-proposed because business logic varies per tenant/vertical, but every plan is validated deterministically before a single tool executes (allowlist membership, schema match, hard-limit check) — the same pattern already proven for the voice receptionist's `_SlotGuard` (model output checked against a prior real tool result before being trusted, `openai_realtime_voice_service.py`, per audit §11). Nothing about *whether* a step is allowed to run is left to the model's own judgment at execution time.

**Explicit prevention design**:

| Risk | Mechanism |
|---|---|
| Hallucinated tools | Plan validation rejects any `tool_name` not in `AgentToolPermission` for that agent — a hard allowlist, not a prompt instruction. |
| Unauthorized tools | Same allowlist, plus the existing RBAC/`ActionPolicy` floor still applies underneath. |
| Cross-tenant access | `ExecutionContext.tenant_id` is set server-side from the authenticated session/agent's own `tenant_id`, never from model output (unchanged from today's `execution_service.py:39-54` pattern — the model never supplies a tenant identifier). |
| Infinite loops | Max-steps + wall-clock timeout on `AgentExecution` (new, §3.4). |
| Duplicate actions | Idempotency-key check across steps within one execution, reusing the idempotency pattern already proven on `createLead`/`createJob`/`triggerInvoiceFromJob` (audit §15). |
| Uncontrolled spending | `financial_risk` tag forces approval at every tier (§3.3); per-execution spend cap in `AgentPolicy`. |
| Duplicate messages | Content-hash dedup per `(agent_id, recipient, day)` (§3.3). |
| Destructive actions | `SYSTEM_BLOCKED_TOOLS` floor (existing, unconditional) plus any new destructive tool must ship with `ActionPolicy=BLOCKED` or `APPROVAL_REQUIRED` by default — never `AUTO`. |
| Prompt injection | Reuse the existing fenced-DATA-block pattern already proven in `ai_provider.py`'s `_SYSTEM_INSTRUCTIONS` (instructs the model to treat embedded content as data, not instructions) for every Context Assembly input (Blueprint text, Knowledge chunks, CompanyMemory) fed to a planning call. |
| Malicious business knowledge | `CompanyMemory`'s existing `PENDING→ACTIVE` human-confirmation gate (verified: AI-proposed memories never reach `ACTIVE`/context-visible status without a human `confirm_memory` call) is the existing control; extend the same gate to Blueprint facts (see `KLAROS_BUSINESS_BLUEPRINT_SPEC.md`). |
| Compromised integrations | Per-integration call caps (§3.3) plus the existing webhook-signature-verification pattern (Stripe real, marketplace HMAC) for any inbound data an agent might read. |

## 5. Website Builder architecture

Rejected approach: prompt → LLM → arbitrary code → production (see `KLAROS_DO_NOT_BUILD_YET.md` §7 for why). Target pipeline:

```
Business Blueprint → Website Requirements (derived: which pages, what forms, what brand
   tokens) → Site Specification (JSON: pages[], nav, brand) → Page Specification (per page:
   sections[]) → Component Specification (per section: component_type + props, from a FIXED,
   reviewed component registry — not generated markup) → Content Specification (copy text,
   LLM-generated but schema-bound to each component's declared text slots) → Brand
   Specification (colors/fonts/logo, derived from Blueprint + tenant upload) → Generated Site
   (SiteVersion row: the fully resolved spec, still not "code") → Preview (renders the spec
   through the fixed component registry on a preview subdomain/route, server-side, same
   registry that will render production) → Validation (schema validation + automated checks:
   every form references a real public_leads-style endpoint, no broken nav links, brand
   contrast/accessibility check) → Human Approval (PublishRequest, reuses ApprovalRequest
   shape) → Publish (SiteVersion.status=PUBLISHED, served by the new website-runtime service,
   see KLAROS_DEPLOYMENT_EVOLUTION_PLAN.md §2)
```

Entities: `Site`, `SiteVersion` (immutable per publish, enabling instant rollback), `Page`, `PageVersion`, `Section`, `Component` (registry reference, not code), `Navigation`, `Form` (declares which backend endpoint it submits to — always `public_leads` or a new equally-narrow `public_*` endpoint, never an arbitrary URL), `SEOMetadata`, `Domain`, `PublishingState`, `BrandSettings`.

**Rendering approach**: component registry (fixed React component set, versioned) driving either SSG output (Next.js `generateStaticParams`-style, preferred for public marketing/lead-gen sites — fast, cacheable, minimal attack surface) or, only where a page genuinely needs per-visitor dynamic content (e.g. a provider-availability widget for Medical Tourism), a thin server-rendered component that itself calls an existing read-only public endpoint. No path generates and executes arbitrary React/Python.

**Forms reuse existing infrastructure, not a new pipeline**: `Website form → Public Lead API (existing public_leads.py, rate-limited) → Lead → Qualification (existing) → CRM → Agent (new, optional) → Appointment (existing appointments.py/Google Calendar sync)`. A generated site's contact/booking form is configuration (which fields map to which `Lead` fields) over the *existing* public lead endpoint, never a new bespoke backend path per tenant site.

## 6. Business Blueprint, Recommendation Engine, Integration Marketplace

Full schemas in `KLAROS_BUSINESS_BLUEPRINT_SPEC.md`, `KLAROS_INTEGRATION_MARKETPLACE_SPEC.md`. Summary of the boundary: Blueprint is the single canonical, mostly-persisted (PostgreSQL) representation of a tenant's business; the Recommendation Engine reads it plus the Integration Marketplace's provider catalog (new metadata table over the existing, unchanged `IntegrationConnection`) to produce `Recommendation` rows with evidence back to specific Blueprint fields — never freeform AI prose presented as a recommendation.

## 7. Knowledge + Memory target distinction

| Concept | Persistence | Authority | Existing/new |
|---|---|---|---|
| Business Facts | `BusinessBlueprint` fields (structured) | Highest — user-confirmed | New |
| Business Knowledge | `KnowledgeFile`/`KnowledgeChunk` (pgvector) | Retrieved, ranked by similarity | Existing, unchanged |
| Business Memory | `CompanyMemory`, `ACTIVE` status only | Human-confirmed rules/preferences | Existing, unchanged — extend population sources to include Blueprint |
| Business State | Live DB rows (CRM/Finance/Ops) | Ground truth of "what is true right now" | Existing, unchanged |
| Conversation Context | `AgentContext` (new, ephemeral per execution) | Scoped to one run | New |
| Agent Working Memory | `AgentExecutionStep` history within one `AgentExecution` | Scoped to one run, not persisted cross-run unless explicitly written to `CompanyMemory` via the existing `propose_memory`→human-`confirm_memory` gate | New, but the promotion path reuses the existing gate |

## 8. Multi-tenancy & security posture (target)

See `KLAROS_SECURITY_EVOLUTION_PLAN.md` for the full migration strategy. Headline target-state additions: Postgres RLS as a backstop (not a replacement) for app-layer filtering; agent-level tenant binding (`Agent.tenant_id` immutable, never settable by the agent's own output); tool-level tenant enforcement (unchanged, already real); object-storage tenant isolation once S3 lands (Phase 0/6); website isolation (separate `website-runtime` origin, §5 above and `KLAROS_DEPLOYMENT_EVOLUTION_PLAN.md` §2); knowledge/vector-search isolation (already real, tenant-scoped pre-filter per `knowledge_retrieval_service.py:11-13`, unchanged).

## 9. Object storage target architecture

See `KLAROS_DEPLOYMENT_EVOLUTION_PLAN.md` §2 and `KLAROS_SECURITY_EVOLUTION_PLAN.md`. Summary: replace `NotConnectedObjectStorageAdapter` with a real S3-compatible client implementing the *same* `ObjectStorageProvider` ABC (`put`/`get`/`delete`, `backend/app/storage/base.py`, verified) already used by `LocalFilesystemStorageAdapter` — no interface change needed, only a new adapter — with tenant-prefixed keys (matching the existing local-adapter convention `{tenant_id}/{uuid}_{filename}`) for job photos/documents, knowledge files, website assets/logos, voice recordings, generated exports.

## 10. Observability target (folded from brief phase 19)

Every new subsystem emits into the *existing* logging/audit primitives, not a parallel stack: `AIInvocationLog` (extended with `agent_execution_id`) for every model call's provider/model/tokens/cost/latency; `AuditLog` for every state-changing action; `AgentExecutionStep` for step-level plan tracing; `Event`/`EventProcessingRecord` (existing dead-letter-capable event log) for async fan-out. "Why did Klaros do this?" = join `AgentExecution` → `AgentExecutionStep` → `AuditLog`/`ApprovalRequest` by `correlation_id`. "What did it cost this business?" = sum `AIInvocationLog.estimated_cost_usd` by `tenant_id`/`agent_id`/date. No new observability *product* (Prometheus/OTel) is required for this to work at the data layer, though one is recommended in `KLAROS_DEPLOYMENT_EVOLUTION_PLAN.md` for operational (not business) metrics.

## 11. End-to-end lifecycle (summary; full stage table in `KLAROS_MEDICAL_TOURISM_VALIDATION.md`/`KLAROS_DROPSHIPPING_VALIDATION.md`)

Register (existing `auth.py`) → Describe Business (new, §Business Discovery) → Discovery/Questions (new) → Business Blueprint (new) → Recommendations (new) → User Approves (existing `ApprovalRequest` pattern, reused) → System Setup (new orchestration, mostly config writes) → Website Creation (new, §5) → Integrations (existing OAuth flows + new marketplace UI) → CRM (existing, unchanged) → Agents (new, §2) → Workflows (existing Automation Engine + new generation layer, §Domain Extensibility) → Test (staging environment, new) → Launch (publish gate, new) → Operate (existing product surface, unchanged) → Optimize (Recommendation Engine feedback loop, new).

## 12. Onboarding UX target — Guided vs Business Map

Both modes read/write the **same** `BusinessBlueprint` and share the same underlying discovery/requirements state — Guided mode is a fixed 10-step linear walk through blueprint sections in a recommended order (reusing the existing onboarding page's step-wizard UI pattern, verified real in `frontend/app/onboarding/page.tsx`); Business Map mode is a non-linear view of the same sections (Customers/Products-Services/Marketing/Sales/Operations/Finance/Communication/Website/Integrations/Agents/Automation/Analytics) letting a user jump directly to any one. Neither mode owns its own copy of the data — both are views over `BusinessBlueprint` + its section-completion state, preventing the two-systems drift the brief warns against.

## 13. Layered diagram (adapted)

```
┌─────────────────────────────── Klaros Experience ───────────────────────────────┐
│ Next.js frontend (existing 57 pages + new: discovery, blueprint, recommendations,│
│ integration marketplace, website builder, agent builder, workflow builder)       │
└───────────────────────────────────────┬──────────────────────────────────────────┘
┌───────────────────────────── Business Intelligence ──────────────────────────────┐
│ Discovery · Requirements · Blueprint · Recommendation Engine (all new)           │
└───────────────────────────────────────┬──────────────────────────────────────────┘
┌──────────────────────────────────── Execution ───────────────────────────────────┐
│ Agent Runtime (new) → AIExecutionService (existing) → ToolRegistry (existing)    │
│ → Automation Engine / Temporal (existing) → Website Builder pipeline (new)       │
└───────────────────────────────────────┬──────────────────────────────────────────┘
┌─────────────────────────────────── Integrations ──────────────────────────────────┐
│ Integration Marketplace metadata (new) over IntegrationConnection (existing);     │
│ Stripe/QuickBooks/Google Calendar/Twilio clients (existing, real)                │
└───────────────────────────────────────┬──────────────────────────────────────────┘
┌───────────────────────────────── Business Domains ────────────────────────────────┐
│ Core (existing): CRM · Jobs · Finance · Marketing · Retention · Compliance        │
│ Vertical extensions (new, additive): Medical Tourism · Dropshipping · future      │
└───────────────────────────────────────┬──────────────────────────────────────────┘
┌────────────────────────────────────── Platform ───────────────────────────────────┐
│ FastAPI · PostgreSQL/pgvector (+RLS, new) · Redis · Temporal · EventBus ·         │
│ RBAC/AuditLog/ApprovalRequest (existing) · Object storage (S3, new adapter)       │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Cross-references

`KLAROS_GAP_ANALYSIS.md`, `KLAROS_AI_AGENT_ARCHITECTURE.md`, `KLAROS_BUSINESS_BLUEPRINT_SPEC.md`, `KLAROS_WEBSITE_BUILDER_SPEC.md`, `KLAROS_INTEGRATION_MARKETPLACE_SPEC.md`, `KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md`, `KLAROS_SECURITY_EVOLUTION_PLAN.md`, `KLAROS_IMPLEMENTATION_ROADMAP.md`, `KLAROS_ARCHITECTURAL_DECISIONS.md`.
