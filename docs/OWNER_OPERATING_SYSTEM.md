# Klaros AI — Owner Operating System (Phase 26)

This document describes the connective layer built in Phase 26 on top of the already-verified business machinery (Phases 1-25): the Owner Attention Queue, the AI Control Center's health signal, and how they connect existing cockpit sections into one coherent answer to "what should I look at right now?"

Phase 26 added exactly **two** new real backend endpoints — `GET /api/v1/dashboard/attention` and `GET /api/v1/dashboard/ai-health` — and one new read-only service, `OwnerAttentionService`. Every other data source on `/dashboard` (CRM metrics, commercial pipeline, operations, finance, marketing, retention, autonomy stats, automation summary, approvals, Company Memory, Morning Brief) is unchanged, pre-existing infrastructure, reused as-is.

## Owner State

The dashboard now answers, in order:

1. **What needs my attention right now?** → the Attention Queue (new, top of page).
2. **What did Klaros automatically do today?** → Autonomy status (pre-existing, Phase 9+).
3. **What does the AI layer look like?** → AI Control Center (extended this phase with a real health signal).
4. **What are my automations doing?** → Automations summary (pre-existing, Phase 24+).
5. **What's my one-paragraph morning summary?** → Morning Brief (pre-existing, Phase 8B+, unified — see below).
6. **Detailed metrics per domain** → CRM/Commercial Pipeline/Operations/Finance/Marketing/Retention sections (all pre-existing).

## Attention Queue

`OwnerAttentionService.get_attention_queue(tenant_id)` (`backend/app/services/owner_attention_service.py`) is a read-only aggregation over the same tables every other real endpoint already reads — no second CRM/finance/automation model. Ten real categories, each backed by a real query:

| Category | Source table | What it means |
|---|---|---|
| `qualified_lead_no_appointment` | `Lead` + `Appointment` | A qualified lead has no appointment booked |
| `quote_stale` | `Quote` | Sent/viewed, no response past the stale-quote threshold |
| `contract_pending` | `Contract` | Sent/viewed, not yet signed |
| `job_qa_failed` | `OperationsException` (type `QA_FAILURE`) | An open QA-failure exception |
| `job_blocked` | `Job` | A job in `BLOCKED` status |
| `invoice_overdue` | `Invoice` | Status `OVERDUE` |
| `retention_opportunity` | `RetentionOpportunity` (non-referral types) | An open retention signal |
| `referral_opportunity` | `RetentionOpportunity` (`REFERRAL_ELIGIBLE`) | An open referral opportunity |
| `ai_approval_required` | `ApprovalRequest` (`PENDING`) | An AI-or-human-requested action awaiting owner approval |
| `ai_feedback_pending` | `CompanyMemory` (`AI_FEEDBACK`, `PENDING`) | Owner feedback from an approval decision awaiting confirmation |
| `automation_failed` | `AutomationExecution` (`FAILED`, last 7 days) | A recent automation execution failure |

Each item is a real row, never fabricated, and carries: `title`, `reason` (built from real field values — amounts, dates, descriptions), `entity_type`/`entity_id`, a `link` to the existing page that already handles that entity (no new pages), `priority`, `score`, and where relevant `age_days`/`monetary_value`.

## Priority Logic

Deterministic, explainable, **no LLM call**. Each category has a base severity score; two adjustments apply where relevant:

- **Age bonus**: `min(age_days * per_day_rate, cap)` — a category-specific rate/cap (e.g. QA failures escalate fast and cap low; retention/referral opportunities escalate slowly).
- **Value bonus**: `+20` if the monetary amount is ≥ $5,000, `+10` if ≥ $1,000, `+5` if > $0 — applied to invoices and quotes only (the categories with a genuine dollar figure).

The composite score maps to a bucket:

```
score >= 80  -> CRITICAL
score >= 55  -> HIGH
score >= 30  -> MEDIUM
else         -> LOW
```

The queue is sorted by score descending. There is no hidden weighting an owner can't reconstruct from the `reason` string alone — that traceability was a deliberate design constraint (Step 4's "keep it explainable").

## Money View

Unchanged — `GET /api/v1/finance/summary` and `GET /api/v1/finance/commercial-pipeline` (both pre-existing, Phase 5/14) already surface: total AR outstanding, overdue invoice count, pending-approval invoice count, total paid, quotes accepted value, deposits awaiting/collected. The Attention Queue adds only the entity-level drill-down (which specific invoice, how overdue, how much) these summaries intentionally omit.

## Sales View

Unchanged — `GET /api/v1/crm/metrics` and the Commercial Pipeline section (Lead → Qualification → Appointment → Quote → Contract → Deposit) are pre-existing. The Attention Queue's `qualified_lead_no_appointment`, `quote_stale`, and `contract_pending` categories are the entity-level version of what those summaries already count in aggregate.

## Operations View

Unchanged — `GET /api/v1/operations/dashboard` (jobs today, unassigned, at-risk, blocked, QA-pending, open exceptions) is pre-existing (Phase 4+). The Attention Queue's `job_qa_failed` and `job_blocked` categories give the entity-level detail.

## Customer / Retention View

Unchanged — `GET /api/v1/retention/summary` is pre-existing (Phase 6). The Attention Queue's `retention_opportunity`/`referral_opportunity` categories surface the specific open `RetentionOpportunity` rows that summary counts.

## AI Control Center

Extended this phase. Previously two conditionally-hidden counts (AI approvals pending, AI feedback pending); now an always-visible section with four tiles:

- **Awaiting approval** — real count from `ApprovalRequest` (unchanged source).
- **Feedback to review** — real count from `CompanyMemory` (unchanged source).
- **AI provider** — `GET /api/v1/dashboard/ai-health` (new, tiny): `AIProvider.is_connected` (a plain attribute check, no network call) plus the provider's own `name`. In this environment it honestly reports **"Not connected" / provider "deterministic"** — no live LLM credential exists (Phase 22's finding, unchanged). This endpoint never claims a live connection it cannot prove.
- **Calls succeeded (24h)** — real count from `AIInvocationLog`, tenant-scoped, last 24 hours. Links to the pre-existing `/ai-activity` page for full detail (no second AI activity view was built).

## Automation Health

Unchanged — `GET /api/v1/automations/summary` (executions running/failed/completed today, scheduled count, enabled/total) is pre-existing (Phase 20+/24). The Attention Queue's `automation_failed` category surfaces the specific failed `AutomationExecution` rows from the last 7 days; clicking one links to `/automations` (Automation Studio), the existing execution-history UI — no second automation UI was built.

## Activity Feed

Not built this phase. Every "what happened" signal an owner needs is already reachable through existing per-domain pages (Job detail's own real timeline, `/ai-activity`, `/automations`' execution history, `/approvals`) — a unified cross-domain feed was evaluated against Step 12's explicit "determine whether Klaros needs" framing and judged not to clear the bar this phase: the Attention Queue already answers "what needs action," and the existing per-entity timelines already answer "what happened to this specific thing." Building a new aggregation table or read model for a chronological cross-domain feed would have been the first genuinely new persistence surface this phase — deferred pending a real, evidenced owner need rather than spec completeness.

## RBAC

The Attention Queue and AI health endpoints are views, not mutations — available to every authenticated role (OWNER, MANAGER, READ_ONLY, TECHNICIAN) via the same `get_current_user` dependency every other read-only cockpit endpoint uses. No new permission was introduced; verified directly (`backend/tests/test_owner_operating_system_e2e.py::test_attention_queue_readonly_and_technician_can_view`) that READ_ONLY, TECHNICIAN, and MANAGER can all successfully load a real, non-empty queue for a tenant with real business state.

## Freshness

No WebSocket or polling infrastructure was added — the existing pattern (fetch on mount, refetch via the existing `load()` callback) is sufficient, matching Step 14's "a simple refresh strategy may be sufficient" guidance. Verified live in the browser: approving a real AI-originated `ApprovalRequest` (dashboard → attention item → `/approvals` → Approve) and returning to `/dashboard` shows the "Awaiting approval" tile drop from 1→0 and a brand-new "Feedback to review" tile appear at 1 (the real Phase 19 learn-loop side effect), with the Attention Queue itself re-ranking to show the new `ai_feedback_pending` item in place of the resolved `ai_approval_required` one — no stale count was shown after the mutation.

## Tenant Isolation

Every query in `OwnerAttentionService` and the AI health endpoint filters on `current_user.tenant_id` — never a client-supplied value. Verified directly: `test_attention_queue_tenant_isolation` and `test_ai_health_tenant_isolation` (both in `test_owner_operating_system_e2e.py`) seed real business state for tenant A and confirm tenant B's queue/health response is empty/zero.

## Performance

`OwnerAttentionService.get_attention_queue` issues one bounded query per category (roughly a dozen total), each filtered by tenant and, where applicable, an indexed status/type column — no N+1 pattern (no per-item follow-up query), no unbounded scan (every table already carries a `tenant_id` index from `TenantScopedMixin`, and result sets are inherently small — open exceptions, pending approvals, etc. are operationally bounded quantities for a single-tenant small business). The `automation_failed` category is additionally time-bounded to the last 7 days. The full response is capped at `limit=50` items.
