# Klaros — AI Agent Architecture

Status: design proposal only. No code in `backend/app/ai/`, `backend/app/tools/`, or `backend/app/models/` was modified to produce this document. This file is the authoritative, most-verified-against-source document in this analysis — every claim about current behavior below was confirmed by direct reads of `backend/app/ai/execution_service.py`, `backend/app/tools/{policy,registry}.py`, and `backend/app/models/organization.py` during this analysis pass (not merely carried over from the prior audit).

## 1. Verified current state (read this before designing anything on top of it)

- `AIExecutionService.request_tool_execution(request, *, tenant_id, ai_role, correlation_id=None)` (`backend/app/ai/execution_service.py:39-54`) performs **no validation itself**. It unconditionally constructs `ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.AI, actor_id=None, role=ai_role, correlation_id=correlation_id)` and calls `ToolRegistry.execute(request.tool_name, request.input, context)`. It is a pure pass-through boundary — correct and intentionally minimal, not incomplete.
- `ToolRegistry.execute()` (`backend/app/tools/registry.py:126-224`) is where every real check happens, identically for human and AI callers: kill-switch (`Organization.ai_paused`) → RBAC permission check → tenant-scope check → AI-usage billing cap (only applies to the one tool flagged `counts_toward_ai_usage=True`) → Pydantic input validation → `PolicyService.resolve()` (tool-level `ActionPolicy`: `AUTO`/`APPROVAL_REQUIRED`/`BLOCKED`, with `SYSTEM_BLOCKED_TOOLS` as an unconditional floor and `TenantToolPolicy` as a per-tenant override) → execute or create `ApprovalRequest` → `AuditLog` write.
- **`Organization.autonomy_level` (`LEVEL_0`..`LEVEL_4`) is stored but read by zero executing code anywhere in the backend.** Its own in-code comment (`organization.py:60-67`) states this explicitly: "remains unenforced/decorative." Treat this field as inert until the Phase 7 work below wires it up. Any design assuming it already gates anything is wrong.
- **No loop/depth/rate protection exists in `ToolRegistry.execute`** beyond the one billing cap noted above. An agent making multiple tool calls per turn has no existing backstop.
- **No `class *Agent` exists anywhere** in `backend/app` (grep-confirmed). No agent registry, no per-agent config object, no plan/execution model.
- Everything above is the correct, narrow foundation to build on — see `KLAROS_DO_NOT_BUILD_YET.md` §4–§5 for why this is extended, not replaced.

## 2. Entity model

See `KLAROS_TARGET_ARCHITECTURE.md` §2.2 for the full table list (`Agent`, `AgentVersion`, `AgentGoal`, `AgentContext`, `AgentMemory`, `AgentToolPermission`, `AgentWorkflow`, `AgentExecution`, `AgentExecutionStep`, `AgentApproval`, `AgentPolicy`). This document covers the *behavior* those tables implement.

`AgentMemory` (not detailed in the target-architecture summary): a narrow, execution-scoped key/value store (`agent_execution_id`, `key`, `value` JSONB) for working state *within* one execution's steps (e.g. "the appointment slot I already checked in step 2") — explicitly **not** a substitute for `CompanyMemory`. Anything an agent learns that should persist beyond one execution must go through the existing `propose_memory` → human `confirm_memory` gate (`company_memory_service.py`, verified real), never be written directly to `AgentMemory` and treated as durable.

## 3. Chain of custody for a single tool call

```
Agent (config: goal, instructions, tool allowlist)
  → AgentGoal (what this execution is trying to achieve)
    → AgentContext (Blueprint + Knowledge + CompanyMemory scope, read-only refs)
      → Allowed Tools (AgentToolPermission rows — the only tools this agent can propose)
        → Permissions (existing RBAC role backing the agent's ai_role, unchanged)
          → Autonomy Level (NEW, per-Agent — see §4; independent of the inert
            Organization.autonomy_level field)
            → Approval Requirements (existing ApprovalRequest machinery, reused
              via new AgentApproval wrapper)
              → Execution Limits (NEW — max steps, wall-clock timeout, per-step
                idempotency check, financial/communication rate limits — see §5)
                → Agent Runtime (NEW AgentExecutionService — orchestrates the
                  loop, calls AIExecutionService once per validated step)
                  → Tool Registry (EXISTING, unchanged, registry.py:126-224)
                    → Audit (EXISTING AuditLog + AIInvocationLog, both
                      additively FK'd to AgentExecution/AgentExecutionStep)
```

This is a literal reuse chain, not a parallel system — every "EXISTING" node above is the same code path a human API request or the voice receptionist already goes through.

## 4. Autonomy system (reconciled design)

Because `autonomy_level` is inert today, the target design introduces autonomy **at the `Agent` level**, not by "turning on" the existing `Organization` field (though `AgentPolicy` may use `Organization.autonomy_level` as an *initial default suggestion* when a tenant creates their first agent — a UX convenience, never itself an enforcement point).

| Tier | Behavior |
|---|---|
| **Observe** | Agent may plan and call read-only tools (tools whose `ActionPolicy=AUTO` and which are additionally tagged `mutates_state=False`, a new per-tool metadata field). Every state-changing step, regardless of the tool's own `ActionPolicy`, is forced into `AgentApproval`. |
| **Recommend** | Agent may execute `AUTO`-policy tools additionally tagged `risk_tier=LOW` (new field) without approval; everything else routes to `AgentApproval`. |
| **Execute-approved** | Agent may execute any tool not `BLOCKED`; each execution still respects the tool's own existing `ActionPolicy` (an `APPROVAL_REQUIRED` tool still requires approval — the agent gains no more trust than a human user of the same role would have). |
| **Execute-autonomous** | Agent may execute `AUTO` and `APPROVAL_REQUIRED` tools without a per-call human gate, **except** the hard ceiling in §5 (financial-risk and destructive actions), which is never bypassable by tier. |

Tier is set on `Agent`/`AgentVersion` (so raising an agent's autonomy is itself a versioned, audited change — reusing the existing `AuditLog`), defaulted and capped per-tenant by `AgentPolicy` (a tenant can restrict the maximum tier any of its agents may hold, but not loosen the hard ceiling in §5).

## 5. Hard limits (tier-independent, enforced in the new `AgentExecutionService`)

- **Financial**: any tool tagged `financial_risk=True` (new field — covers refunds, write-offs, payouts, Stripe charges/checkout creation above a configurable per-execution cap on `AgentPolicy`) always requires `AgentApproval`.
- **Communication**: outbound-message tools are rate-limited per `(agent_id, recipient)` per day and content-hash-deduplicated within a rolling window, preventing runaway or duplicate customer messaging.
- **Integration**: per-integration call-volume ceiling per `AgentExecution`, implemented the same way the existing AI-usage billing cap is implemented (`BillingService.check_ai_usage_allowed` pattern, `registry.py:169-178`) — extended to cover any tool tagged as integration-calling, not just the one Morning Brief tool it covers today.
- **Execution**: `max_steps` (default from `AgentPolicy`, hard ceiling regardless of tenant override), wall-clock timeout, and an idempotency-key check across all steps within one `AgentExecution` (new — nothing like this exists today; reuses the idempotency-key *pattern* already proven on `createLead`/`createJob`/`triggerInvoiceFromJob`, per the audit, but applied at the plan level for the first time).
- **Prohibited, unconditionally**: `SYSTEM_BLOCKED_TOOLS` (existing floor: `operations.delete_job`, `customer.delete`, `finance.delete_invoice`) — no autonomy tier, tenant policy, or agent configuration can override this list. Any *new* destructive tool introduced for Blueprint/Website/Marketplace/vertical extensions must default to `BLOCKED` or `APPROVAL_REQUIRED`, never `AUTO`, at time of registration.

## 6. Orchestration loop (`AgentExecutionService`, new)

1. Receive trigger (user chat turn, scheduled `AgentWorkflow`, or an event the agent is subscribed to via `EventBus`).
2. Assemble `AgentContext` (Blueprint fields + relevant `KnowledgeChunk` retrieval + `ACTIVE` `CompanyMemory` rows for the tenant) — tenant-scoped reads only, using the existing tenant-scoped retrieval services unchanged.
3. Call the configured AI provider (existing `ai_provider.py` abstraction) with the agent's `instructions` + assembled context + the *schema* of only the tools in `AgentToolPermission` for this agent (reusing the existing `input_schema.model_json_schema()` conversion pattern already proven in `openai_realtime_voice_service.py:124-143`, generalized beyond the voice path) to produce a candidate plan (ordered list of `{tool_name, input}`).
4. **Plan validation (deterministic, before any execution)**: every `tool_name` ∈ this agent's `AgentToolPermission`; every `input` validates against that tool's own Pydantic schema; step count ≤ `max_steps`; no step's tool is in `SYSTEM_BLOCKED_TOOLS`. A plan failing validation is rejected outright — the agent does not get a retry loop that silently drops the invalid step; the execution is marked `FAILED` with the validation reason recorded, and a fresh plan is only generated on a new explicit trigger.
5. Persist the validated plan on `AgentExecution.plan`.
6. For each step, in order: check hard limits (§5) and tier rules (§4) → if approval required, create `AgentApproval`/`ApprovalRequest` and pause the execution (`status=AWAITING_APPROVAL`) until a human decides → on approval (or if no approval needed), call `AIExecutionService.request_tool_execution` exactly as it exists today → record `AgentExecutionStep` (input, output, status, timing) → run a deterministic post-condition check where applicable (reusing the voice receptionist's `_SlotGuard` pattern: verify the tool's actual output matches what the plan claimed it would do, e.g. confirm a created appointment's time matches the plan's stated slot) → on step failure, halt the execution (`status=FAILED`) rather than silently continuing to the next step.
7. On all steps complete: `status=SUCCEEDED`, publish an `agent.execution_completed` event (new `EventType`, existing `EventBus`).

## 7. Observability

Every execution is traceable end to end via `correlation_id` shared across `AgentExecution`, `AgentExecutionStep`, `AIInvocationLog` (extended with a nullable `agent_execution_id` FK), `ApprovalRequest`/`AgentApproval`, and `AuditLog` — answering "why did Klaros do this" as a single-`correlation_id` join across tables that already exist today (only `AIInvocationLog` needs one additive nullable column). Cost is answered by summing `AIInvocationLog.estimated_cost_usd` (already tracked, already honestly `NULL` when not computed rather than fabricated, per the verified field list) grouped by `agent_id`/`tenant_id`/date.

## 8. Migration impact

All new tables, additive-only. Two additive nullable columns on existing tables: `AIInvocationLog.agent_execution_id`, and a small set of new per-tool metadata fields (`risk_tier`, `mutates_state`, `financial_risk`) added to the existing `DEFAULT_TOOL_POLICIES`-adjacent tool metadata (implementation detail: likely a new `ToolMetadata` table keyed by `tool_name` rather than touching `policy.py`'s existing static dict directly, to avoid a large diff to a safety-critical, already-correct file — see `KLAROS_DATABASE_EVOLUTION_PLAN.md`). No existing table, enum, or service method is modified. Rollback = drop new tables, drop the two additive columns; zero impact on existing tool execution for human or the existing three narrow AI call sites (Morning Brief, lead-qualification advisory, voice receptionist), which continue to call `AIExecutionService` exactly as before.

## 9. Risks

- **Tier misconfiguration**: a tenant setting every agent to Execute-autonomous defeats the purpose of tiers. Mitigation: `AgentPolicy` tenant-level cap (§4), defaulting new tenants to Recommend.
- **Plan-validation bypass via a compromised/buggy provider response**: mitigated by validation being fully deterministic and independent of the model's own claims (step 4 never trusts the model's assertion that a step is "safe").
- **Approval fatigue** (too many `AgentApproval`s train users to rubber-stamp): mitigated by tier design funneling only genuinely risky actions to approval at higher tiers, and by grouping same-execution approvals into one review screen rather than one notification per step.

## Cross-references

`KLAROS_TARGET_ARCHITECTURE.md` §2–§4, `KLAROS_GAP_ANALYSIS.md` §1 (P0 items 1.1, 1.3, 1.4), `KLAROS_DO_NOT_BUILD_YET.md` §4–§5, `KLAROS_ARCHITECTURAL_DECISIONS.md` ADR-002/ADR-003, `KLAROS_SECURITY_EVOLUTION_PLAN.md` §Agent-level tenant binding.
