# Klaros AI — Final Agent Architecture and Governance Model

Covers items C (governance model), F (agent architecture vs. existing `ToolRegistry`/`AIExecutionService`), and G (MCP decision).

## Terminology (enforced throughout this document set)

**"AI"** = a model producing text/structured output (an `AIProvider` call — Anthropic/OpenAI/etc., real today via `backend/app/services/ai_provider.py`, raw `httpx`, no SDK dependency).

**"Agent"** = identity + purpose + instructions + declared tools + permissions + autonomy tier + memory/knowledge context + triggers + execution limits + approval policy + audit trail + version + lifecycle status. An Agent *uses* AI calls as one of its capabilities; an Agent is not itself a model. This distinction is currently blurred in the codebase because no Agent concept exists yet — every "AI action" today (automation next-action, morning brief, voice) is deterministic service logic that constructs `ToolRequest`s directly, calling the AI provider only for text generation, not for open-ended tool selection. The one exception is the realtime voice path, where the model does choose tools via the OpenAI Realtime API's function-calling, validated against a fixed schema before `AIExecutionService.request_tool_execution()` is called (`openai_realtime_voice_service.py:520-526`).

## Can the existing `ToolRegistry` become the Agent execution capability layer without rebuilding it? — YES, and here is exactly how

`ToolRegistry.execute()` (`backend/app/tools/registry.py:126-224`) already performs: kill-switch → RBAC → tenant-scope → billing cap → schema validation → policy resolution (AUTO/APPROVAL_REQUIRED/BLOCKED) → execute → audit. This pipeline is actor-agnostic today — it takes an `ExecutionContext` with an `actor_type` (`USER` or `AI`) and doesn't care what produced the `ToolRequest`.

**What must change, precisely:**
1. Extend `ActorType` with `AGENT` (additive enum value, no migration to existing rows since it's a code-level enum, not a DB column with a fixed value set — verified: `role`/actor fields in this codebase are `String`/`StrEnum` columns, not Postgres native enums, so adding a value is a code change only).
2. Add two new checks to `ToolRegistry.execute()`, inserted **before** the existing RBAC check (step 3 in the current pipeline), never replacing or reordering the existing nine steps:
   - **Agent tool permission check**: does `AgentToolPermission` grant this `agent_id` access to this `tool_name`? (New table, new check function, same shape as the existing RBAC check function.)
   - **Agent autonomy tier check**: does the agent's tier (Observe/Recommend/Execute-with-approval/Execute-autonomous) allow this tool's declared risk class to proceed without forcing `APPROVAL_REQUIRED`, regardless of what the tool's own `ActionPolicy` would otherwise allow? This check can only **narrow** — an agent below Execute-autonomous tier can never bypass an approval the tool's own policy would already require, and `financial_risk`-tagged tools and the existing `SYSTEM_BLOCKED_TOOLS` floor are never bypassable by any tier.
3. Build `AIExecutionService` up, not sideways: today it's a thin pass-through (`execution_service.py:39-54`docstring: "Phase 2 does not build the autonomous agent that produces ToolRequests"). The new `AgentExecutionService` sits **above** it — it is the thing that decides *which* tool to call and with *what* arguments (via an AI provider call constrained to the agent's declared tool schema, i.e. tool-calling), then hands the resulting `ToolRequest` to the existing `AIExecutionService.request_tool_execution()` unchanged, which still calls `ToolRegistry.execute()` unchanged.

No part of `ToolRegistry`, its 233 tools, or its existing 9-step pipeline needs to be rebuilt, forked, or wrapped in a second framework. This is the concrete technical basis for the "extend, don't replace" verdict throughout this document set.

## Governance chain — Organization → Agent → Agent permissions → Agent autonomy → Tool policy → Approval policy → Execution → Audit

Full enforcement order for every execution request under an Agent (extends, does not replace, the existing 9-step `ToolRegistry.execute()` sequence):

1. **Tenant isolation** — `ExecutionContext.tenant_id`, sourced from the authenticated actor's JWT (for a User-triggered agent run) or the Agent's own `tenant_id` (for a scheduled/triggered run) — never from client input.
2. **Authenticated actor** — the human or scheduler that initiated the agent run must itself be authenticated; an agent can never be invoked anonymously, even for scheduled triggers (a scheduled trigger runs as a designated service-actor bound to the agent's `tenant_id`, itself audited).
3. **Agent identity** — resolve `Agent` by id, confirm `status == ACTIVE` and `tenant_id` matches context (an agent belonging to another tenant can never be resolved, even by id guess — enforced by the same tenant-scoped-query convention as everything else today, and by RLS once Phase 0 lands).
4. **Agent permission** — `AgentToolPermission` check (new, described above).
5. **Autonomy level** — Agent tier check (new, described above); this is the layer that replaces the dead `Organization.autonomy_level` functionally, while that field itself is deprecated to a non-enforcing compatibility field (KLAROS_ARCHITECTURE_REVIEW.md §5).
6. **Tool availability** — does the tool exist, is it registered, is it not globally blocked (`SYSTEM_BLOCKED_TOOLS`, existing).
7. **Tool policy** — existing `PolicyService` resolution (AUTO/APPROVAL_REQUIRED/BLOCKED), unchanged.
8. **Approval requirement** — union of the tool's own policy result and the agent tier's ceiling; whichever is stricter wins. Creates `ApprovalRequest` (existing table/flow) if required.
9. **Rate/concurrency limits** — new: per-agent execution-count and concurrent-execution ceilings (`AgentVersion.max_executions_per_hour`, `max_concurrent_executions`), checked before invoking the tool; a loop/depth-protection check specifically for agent-initiated tool chains (max chain depth, e.g. 5, to prevent an agent recursively triggering itself via automation events it just created — a concrete instance of the "no loop/depth protection" P0 gap identified in KLAROS_GAP_ANALYSIS.md).
10. **Execution** — unchanged `tool.execute()` call.
11. **Audit** — unchanged `AuditLog` write, extended to include `agent_id`/`agent_execution_id` when the actor is an Agent.
12. **Failure handling** — unchanged error/exception paths in `ToolRegistry`, extended so an Agent's failed execution updates `AgentExecution.status = FAILED` and, per the agent's configured failure policy, either halts the agent run, retries (bounded), or surfaces to the approval/notification queue for human intervention — never silently retries unbounded.

**Bypass-path audit:** grep confirms all 233 existing tools are invoked exclusively through `ToolRegistry.execute()` — no direct `tool.execute()` call site exists outside the registry. The new Agent checks are inserted into this single choke point, so there is no route by which an Agent-initiated action can reach a tool without passing through the full chain above. This was independently verified, not assumed.

## Agent model — full specification

`Agent` (tenant-scoped): `id`, `name`, `purpose` (free text), `instructions` (system-prompt-equivalent, versioned via `AgentVersion`), `status` (DRAFT/ACTIVE/PAUSED/ARCHIVED), `autonomy_tier`, `current_version_id`, `created_by`.

`AgentVersion` (tenant-scoped, immutable once referenced by any `AgentExecution`): `agent_id`, `version`, `instructions_snapshot`, `tool_permissions_snapshot` (denormalized copy of `AgentToolPermission` at publish time, for reproducibility even if permissions later change), `memory_refs` (which `CompanyMemory`/`KnowledgeFile` categories this agent may read), `triggers` (schedule cron expression and/or event-type subscriptions), `limits` (`max_executions_per_hour`, `max_concurrent_executions`, `max_tool_chain_depth`), `approval_policy_override` (can only narrow, never widen, the tool-level policy), `status` (DRAFT/PUBLISHED/DEPRECATED).

`AgentExecution` (tenant-scoped): `agent_id`, `agent_version_id`, `trigger_source` (manual/scheduled/event), `status` (RUNNING/COMPLETED/FAILED/HALTED), `started_at`/`completed_at`, linked `AIInvocationLog` rows and `AuditLog` rows.

`AgentToolPermission` (tenant-scoped join table): `agent_id`, `tool_name`, optional `constraint` (e.g. read-only subset).

**Memory:** an agent reads (never writes directly to) `CompanyMemory` and `KnowledgeFile`/`KnowledgeChunk` via declared `memory_refs` — it can *propose* new memory (existing `propose_memory` tool, human-confirmed), never write ACTIVE memory itself, matching the existing enforced pattern in `company_memory_service.py`.

**Knowledge vs. Memory vs. Configuration** (item O): Knowledge = what the business knows (`KnowledgeFile`/`KnowledgeChunk`, documents/FAQs/policies, searchable via embeddings). Memory = what an agent/system has learned about execution/context (`CompanyMemory`, structured keyed facts, propose/confirm gated, no embedding by design). Configuration = what was explicitly set by a human (`Agent`/`AgentVersion`, `BusinessBlueprint`, `IntegrationConnection` settings). These three are never conflated: an agent's instructions live in `AgentVersion` (configuration), the facts it can recall about the business live in `CompanyMemory` (memory), and the documents it can search live in `KnowledgeFile` (knowledge) — three distinct tables, three distinct write paths, three distinct trust levels.

**Lifecycle:** DRAFT (editable, not executable) → ACTIVE (executable, versions immutable once run) → PAUSED (existing runs may complete, no new runs) → ARCHIVED (read-only, historical). Failure handling per execution is per §Governance chain step 12.

**Workflow Generation (item N) via Agents:** a workflow like "new lead → qualification agent → CRM update → email → calendar booking → human notification" is expressed as a `Workflow`/`WorkflowVersion` spec (KLAROS_FINAL_DOMAIN_MODEL.md) whose steps reference an `Agent` invocation for the "qualification" step and existing deterministic `Automation` actions for the rest — compiled into the existing Automation Engine for simple trigger-chains or a Temporal workflow definition for anything requiring durable waits/compensation (e.g. "wait up to 48h for calendar booking, then escalate"). This reuses both existing execution engines; it does not add a third.

## MCP decision — **DO NOT BUILD**

Evaluated against: existing `ToolRegistry` (233 tools, already governed), internal tools (all first-party, tenant-aware by construction), third-party integrations (adapter pattern already exists, credential-encrypted, adding MCP would mean re-implementing tenant isolation and RBAC enforcement *inside* a generic protocol never designed for this domain's approval/audit requirements), agent runtime (the Agent Runtime above is designed specifically to sit on `ToolRegistry` — MCP would be a second, parallel tool-invocation path), external tool ecosystems (no concrete near-term need identified in any of the 28 documents or in this review — Klaros's tool surface is entirely first-party business logic, not generic developer tooling), security (MCP servers as commonly deployed are a new trust boundary and a new place tenant-scoping could be forgotten), observability (would require re-plumbing the existing `AuditLog`/`AIInvocationLog` integration), credential handling (would require a second credential-passing mechanism alongside the existing Fernet-encrypted `IntegrationConnection` store).

**Reasoning for DO NOT BUILD (not merely BUILD LATER):** MCP solves the problem of connecting an agent to *tools it doesn't already have first-party access to* — Klaros's actual problem is the opposite: it already has 233 first-party tools and needs *governance* over them, which MCP does not provide out of the box. Adopting MCP now would mean re-proving tenant isolation, RBAC, approval-gating, and audit logging inside a third-party abstraction not designed for them, for zero net-new capability. If a genuine future need emerges — e.g., Klaros needs to expose its own tools to an *external* agent (a customer's own AI assistant) — that is a different, narrower question ("should Klaros expose an MCP *server* over a subset of its already-governed tools") and should be evaluated separately, on its own merits, only after the Agent Runtime itself is live and audited. This review upholds the existing ADR-002 rejection and finds no new evidence to revisit it.
