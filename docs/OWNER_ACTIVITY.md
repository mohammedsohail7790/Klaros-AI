# Klaros AI — Owner Activity Feed (Phase 27)

## Purpose

Answers one question: **"What happened across my entire business?"** — without opening Leads, Quotes, Contracts, Jobs, Finance, Retention, Automations, Approvals, or Memory individually. It sits alongside (not instead of) the Phase 26 Attention Queue: the Attention Queue answers *"what needs me?"*, the Activity Feed answers *"what happened?"*. Both stay on `/dashboard`, in that order.

This is a **read-only aggregation and presentation layer**. No new event bus, event store, audit system, activity database, workflow engine, notification system, or AI engine was created. The feed is never a source of truth — if a source row changes or is deleted, the feed reflects that current state on the next read; nothing is cached or duplicated.

## Source Systems

`OwnerActivityService` (`backend/app/services/owner_activity_service.py`) queries fifteen bounded, tenant-scoped `select()`s directly against existing tables — the exact same discipline `OwnerAttentionService` (Phase 26) established. Full source-of-truth matrix (also in the module's own docstring):

| Activity type | Source table | Timestamp column |
|---|---|---|
| `LEAD_CREATED` | `Lead` | `created_at` |
| `LEAD_QUALIFIED` | `Lead` | `updated_at` (qualification_status=QUALIFIED) |
| `APPOINTMENT_CREATED` | `Appointment` | `created_at` |
| `QUOTE_CREATED` | `Quote` | `created_at` |
| `QUOTE_EXPIRED` | `Quote` | `updated_at` (status=EXPIRED) |
| `CONTRACT_CREATED` | `Contract` | `created_at` |
| `CONTRACT_SIGNED` | `Contract` | `decided_at` (status=SIGNED) |
| `JOB_CREATED` | `Job` | `created_at` |
| `QA_FAILED` | `OperationsException` (type=QA_FAILURE) | `created_at` |
| `QA_PASSED` | `JobQA` (status=PASSED) | `updated_at` |
| `SIGNOFF_COMPLETED` | `CustomerSignoff` | `signed_at` |
| `INVOICE_CREATED` | `Invoice` | `created_at` |
| `INVOICE_OVERDUE` | `Invoice` (status=OVERDUE) | `updated_at` |
| `PAYMENT_RECEIVED` | `Payment` | `received_at` |
| `RETENTION_OPPORTUNITY` | `RetentionOpportunity` (non-referral types) | `detected_at` |
| `REFERRAL_OPPORTUNITY` | `RetentionOpportunity` (type=REFERRAL_ELIGIBLE) | `detected_at` |
| `AUTOMATION_EXECUTED` | `AutomationExecution` (status=COMPLETED) | `completed_at` |
| `AUTOMATION_FAILED` | `AutomationExecution` (status=FAILED) | `completed_at` |
| `AI_DECISION_PROPOSED` | `ApprovalRequest` (requested_by_type=AI) | `created_at` |
| `AI_ACTION_APPROVED` | `ApprovalRequest` (status=APPROVED, requested_by_type=AI) | `decided_at` |
| `AI_ACTION_EXECUTED` | `AuditLog` (actor_type=AI, result=success, tool is not null) | `created_at` |
| `AI_FEEDBACK_PENDING` | `CompanyMemory` (memory_type=AI_FEEDBACK, status=PENDING) | `created_at` |
| `AI_FEEDBACK_CONFIRMED` | `CompanyMemory` (memory_type=AI_FEEDBACK, status=ACTIVE) | `updated_at` |

**Deliberately not implemented**: `JOB_STATUS_CHANGED`. `Job.updated_at` alone cannot say *which* status changed, or distinguish a real transition from an unrelated field edit, without adding a job-status-history table — which this phase was explicitly told not to introduce. `JOB_CREATED` plus the QA/signoff/invoice milestones already cover the operationally meaningful moments in a job's life.

`AI_ACTION_EXECUTED` deliberately reads `AuditLog` rather than `ApprovalRequest` — it's the one activity type with no single dedicated domain table, since an AUTO-policy AI action executes directly through the ToolRegistry with no `ApprovalRequest` row ever created. `AuditLog` is filtered to `tool IS NOT NULL` specifically to exclude internal service-level audit calls (e.g. `memory.propose`) that aren't a governed tool execution and are already represented by their own dedicated type (`AI_FEEDBACK_PENDING`) — verified this avoided a real duplicate-noise bug found during manual testing (a `memory.propose` audit row with no `tool` value was producing a nonsensical "Klaros AI executed: None" line before the filter was added).

## Activity DTO

One canonical shape (`Activity` dataclass / `ActivityItemOut` Pydantic model): `id` (a stable synthetic string, `f"{type}:{row_id}"` — never a new primary key), `timestamp`, `activity_type`, `category`, `title`, `description`, `severity` (INFO/WARNING/ERROR), `actor_type`, `actor_name`, `entity_type`, `entity_id`, `entity_label`, `link`, `status`, `metadata`. Only safe, already-elsewhere-visible fields are included — no raw AI prompts/responses, no secrets, no internal tokens. `AI_FEEDBACK_*` descriptions reuse `CompanyMemory.value`, the exact same text `/settings/memory` already shows; nothing new is exposed.

## Categories

Ten: `CRM`, `SALES`, `CONTRACT`, `OPERATIONS`, `QA`, `FINANCE`, `RETENTION`, `REFERRAL`, `AUTOMATION`, `AI`. Filtering is applied after merge, so it stays correct regardless of which source(s) match.

## Ordering & Pagination

Deterministic: `timestamp` descending, then `id` ascending as a stable tiebreaker for identical timestamps. Bounded, page/offset-style pagination — `page` (≥1) and `page_size` (1-100, default 25), both `Query(...)`-validated by FastAPI so an out-of-range request is rejected with `422`, never silently clamped to something unbounded. Each of the fifteen source queries is independently capped at 100 rows before merging, so total work per request is bounded regardless of table size — no N+1 pattern, no unbounded scan.

## Filters

`category` (one of the ten above) — the only filter implemented, per the mission's own "do not build an elaborate analytics query language" instruction. `entity_type` filtering was considered but not added; no evidenced need surfaced during implementation, and it can be added later without a breaking change if a real need appears.

## Tenant Isolation

Every one of the fifteen source queries filters on the `tenant_id` parameter, which the API layer sources exclusively from `current_user.tenant_id`. There is no `tenant_id` query parameter on the endpoint at all — passing one is simply ignored by FastAPI's parameter binding, proven directly (`test_activity_feed_client_cannot_supply_tenant_id`). Verified: `test_activity_feed_tenant_isolation` — tenant B's feed is empty against a tenant A seeded with a full realistic business history.

## RBAC

Read-only view, available to every authenticated role via the same `get_current_user` dependency every other cockpit endpoint uses — no new permission was introduced. Verified: `test_activity_feed_rbac_read_only_and_technician` — READ_ONLY, TECHNICIAN, and MANAGER all see the real, non-empty feed. No mutation route exists (`POST /api/v1/dashboard/activity` → `405`, verified directly).

## Security

No raw AI prompts, responses, API keys, provider credentials, or internal tokens appear anywhere in a serialized `Activity` — verified programmatically (`test_activity_feed_aggregates_real_mixed_business_state` scans every returned item's full string representation for a list of sensitive substrings and asserts none appear). `AuditLog.input_summary` (which already redacts sensitive fields at write time, per `app/tools/redact.py`) is never even read by this service — only `tool`/`actor_type`/`result`/`entity_type`/`entity_id`/`created_at` are selected, exactly the same safe-fields discipline every other AuditLog consumer in this codebase already follows.

## Source-of-Truth Principle

The feed is generated fresh on every request directly from the tables above — it holds no state of its own. If a `Quote` is later deleted or a `CompanyMemory` row is reassigned, the next feed request reflects that immediately; there is nothing to go stale or drift out of sync, because there is nothing persisted beyond the source tables themselves.

## Dashboard Integration

Positioned last, after all detailed business-metric sections, per the mission's own recommended order: (1) Needs Your Attention, (2) Autonomy / AI Control, (3) Business summary (CRM/Pipeline/Operations/Finance/Marketing/Retention), (4) Recent Activity. A compact, paginated (10 per page) list with a category filter — verified live in the browser: real chronological history rendered, category filter narrowed correctly, and clicking an item with a real link navigated to its actual source page (e.g. a QA-failure item → the real Job detail page).
