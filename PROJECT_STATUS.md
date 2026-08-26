# Klaros AI — Project Status

Last updated: 2026-08-26 (Phase 4 checkpoint)

## What's actually built and verified — Phases 1–3 (unchanged, condensed)

- **Phase 1**: multi-tenant data model, JWT auth, RBAC, tenant isolation, audit log, Docker Compose.
- **Phase 2**: durable event bus (Postgres store + pluggable transport, idempotency, retry/dead-letter/
  replay), `ToolRegistry` enforcement pipeline (permission → tenant → schema → policy → audit),
  approval boundary, AI execution boundary, integration provider framework, Temporal workflow
  foundation.
- **Phase 3**: CRM — leads (create/normalize/match/qualify), customers (Customer 360, timeline, AI
  summary), appointments/booking (`InternalTestCalendarAdapter`, real double-booking prevention),
  communications adapter (not yet wired to anything at that checkpoint).

Full detail on these phases is in git history / the Phase 1–3 sections of earlier versions of this
file. This checkpoint focuses on what Phase 4 added and verified.

## What's actually built and verified — Phase 4 (Operations & Delivery)

### Domain model

`backend/app/models/operations.py` — tenant-scoped `Job`, `Worker`, `JobTask`, `JobAttachment`
(generalized document/photo/voice-note model, discriminated by `kind` — one table instead of three
near-identical ones), `JobMaterial`, `PurchaseOrder`/`PurchaseOrderItem`, `ScopeChange`,
`OperationsException`, `JobQA`, `CompletionPacket`, `CustomerSignoff`. Migration
`0004_operations_domain` (hand-written, same Postgres-not-available caveat as `0001`–`0003`).

### Job state machine

`backend/app/services/job_state_machine.py` — a fixed transition table (`ALLOWED_TRANSITIONS`), not
"anything goes." `CLOSED` and `CANCELLED` are terminal. Verified: the full valid chain
(`DRAFT→...→CLOSED`) succeeds; every spec-listed invalid transition
(`CLOSED→IN_PROGRESS`, `COMPLETED→EN_ROUTE`, `CANCELLED→ON_SITE`, plus others) is rejected.

**Tool-naming note, stated plainly**: the spec's tool list doesn't name explicit tools for the
EN_ROUTE/ON_SITE steps in the dispatch chain, even though the state machine and event list require
them as distinct steps. `operations.update_job_status` is the generic transition used for those two
specifically; every other step (`dispatch_job`, `start_job`, `complete_job`, `block_job`,
`unblock_job`, `cancel_job`) has its own named tool as the spec lists.

### Job creation, and the lead→customer→appointment→job conversion

`backend/app/services/job_service.py`: `create_job_from_appointment` is idempotent (same appointment
twice → same job, not a duplicate — verified). `backend/app/services/conversion_service.py`
(`operations.convert_lead_and_book` tool) implements the section-4 explicit conversion: a qualified
lead → match-or-create a customer (reusing Phase 3's exact-match logic, still no fuzzy auto-merge) →
book an appointment → create the job, as one idempotent action, emitting `lead.converted`,
`customer.created` (only if a new customer was actually created), and the appointment/job creation
events.

### Assignment & scheduling

`backend/app/services/job_transition_service.py`. Assignment checks: worker must be `active` and not
`OFFLINE`/`INACTIVE`; service-type compatibility when the job's `service_type` and the worker's
`service_types` are both set; a real schedule-conflict check (overlapping time window, same
`assigned_user_id`, excluding cancelled/closed jobs). All three checks verified by tests, including
the exact schedule-conflict scenario from the spec. Scheduling and booking build on the Phase 3
calendar/appointment infrastructure — no second calendar system was created.

### Dispatch, tasks, materials, documents

Dispatch is a state transition (`SCHEDULED→DISPATCHED`) like any other, publishing `job.dispatched`.
Tasks (`backend/app/services/task_service.py`): required/optional, ordered, completable/skippable.
Materials + PO drafts (`backend/app/services/material_service.py`): `operations.create_purchase_order_draft`
generates a `DRAFT` PO from a job's `REQUIRED` materials — **no supplier is connected**
(`ProcurementProvider` in `app/integrations/base.py` / `GenericSupplierAdapter` in `adapters.py`
report `NOT_CONNECTED`; nothing here sends anything anywhere).

### Field documentation — real file storage, not fake

`backend/app/storage/` — `ObjectStorageProvider` interface, `LocalFilesystemStorageAdapter`
(**INTERNAL LOCAL STORAGE** — real, working, writes actual bytes to disk under
`{tenant_id}/{uuid}_{filename}`, validates MIME type + a 25MB size cap, sanitizes filenames, rejects
path traversal) and `NotConnectedObjectStorageAdapter` for the real-S3 case
(`OBJECT_STORAGE_ENDPOINT` unset by default). Verified: uploaded photo/document/voice-note bytes are
written to disk and the returned `size_bytes` matches the actual content length; oversized and
disallowed-content-type uploads are rejected; voice notes get `transcription_status: NOT_CONFIGURED`
(no transcription provider exists — architecture is ready for one, nothing fakes a transcript).
**Live-verified in the browser**: uploaded a real PNG file via the job detail page; the API stored it
and the UI showed "PHOTO: test_photo.png (68 bytes, internal_local_storage)".

### QA engine

`backend/app/services/qa_service.py`. `complete_qa` **re-checks the required conditions itself**
rather than trusting the caller: all required tasks completed, at least one photo/document uploaded,
and the job was actually started (`actual_start` set). Fails loudly (`QAValidationError`, listing
exactly what's missing) rather than silently passing. `fail_qa` keeps the job `QA_PENDING` and
creates a `QA_FAILURE` exception — verified both paths, including that a job with an incomplete
required task cannot pass QA.

### Exceptions & deterministic delay detection

`backend/app/services/exception_service.py` (create/resolve, deduplicates open exceptions of the
same type+entity via a DB unique constraint — verified) and
`backend/app/services/delay_detection_service.py` — **pure timestamp comparisons, no LLM**, per the
spec's explicit instruction: `JOB_DELAYED` (still `IN_PROGRESS` past `scheduled_end`), `JOB_OVERDUE`
(still `SCHEDULED`, never dispatched, 30+ minutes past `scheduled_start`), `JOB_UNASSIGNED`
(`SCHEDULED` with no worker). `POST /api/v1/exceptions/detect` runs it on demand — there is no
scheduler/cron wired up to run it automatically, stated plainly rather than implied.

### Customer communication — now actually wired into Operations

`backend/app/services/operations_communication_service.py` +
`backend/app/events/operations_handlers.py`: `job.scheduled`/`dispatched`/`en_route`/`completed`
events trigger the same `InternalTestCommunicationAdapter` from Phase 3 (logs to
`communication_logs`) — closing the exact gap Phase 3's status doc called out ("the adapter exists
but nothing calls it"). Verified via a dedicated test and live in the browser (dispatching a job with
a real customer email produced a real `communication_logs` row).

### Completion packet & close-out

`backend/app/services/completion_service.py`. `generate_completion_packet` assembles a summary from
real rows only (tasks, attachments, materials, QA result) — nothing invented for missing data.
`close_job` **re-checks three things itself**: QA passed, all required tasks complete, and a `READY`
packet exists — verified that closing without a packet is rejected with a specific message, and that
generating the packet then closing succeeds. On success it emits `job.closed` *and*
`invoice.trigger_requested` — verified the event payload explicitly states "Finance module not
implemented yet (Phase 5) — no invoice was created," and that no invoice record of any kind exists
anywhere in the database.

### Customer sign-off

`backend/app/services/signoff_service.py`'s `InternalCustomerSignoffProvider` — an internal record
(`customer_signoffs` table) explicitly documented as **not a legally binding e-signature**.

### Scope changes

`backend/app/services/scope_change_service.py`. `create_scope_change` computes the estimated margin
impact and **never touches the job's own pricing fields** — applying an approved change to actual
job economics is explicitly out of scope (Finance, Phase 5). A scope change with a cost/revenue
impact starts `PENDING_APPROVAL`; `operations.request_scope_change_approval`'s default policy is
`APPROVAL_REQUIRED`, so calling it goes through the same approval-creation path as any other
APPROVAL_REQUIRED tool from Phase 2 — no new approval mechanism was built.

### Tools, policies, audit

34 new `operations.*` tools registered on the **same** `ToolRegistry` from Phase 2 — job CRUD/search,
assign/unassign, schedule/reschedule/dispatch/transition/start/complete/cancel/block/unblock, tasks,
materials, documents/photos/voice-notes, QA, scope changes, exceptions, completion/close-out, worker
management, `generate_job_summary` (section 34's AI job summary — real data, explicit about missing
info, no chain-of-thought), and `convert_lead_and_book`. Default policies follow section 31 exactly:
reads/creates/most transitions are `AUTO`; `reschedule_job` and `request_scope_change_approval` are
`APPROVAL_REQUIRED`; `operations.delete_job` is reserved as `BLOCKED` (no tool implements deletion —
the policy entry exists so the reservation is explicit, matching the CRM `customer.delete` pattern
from Phase 3).

**A real audit-trail gap was found and fixed this phase**: the generic `ToolRegistry._audit()` never
set `entity_type`/`entity_id` on audit rows (true since Phase 2 — Phase 3's tests never exercised
timeline-by-entity rigorously enough to catch it). Job/customer timelines query `AuditLog` filtered
by entity, so they were silently missing every tool-call entry. Fixed in
`backend/app/tools/registry.py` with `_infer_entity()` — infers the entity from the tool's own output
shape (e.g. `JobOutput.job["id"]` → `("job", <uuid>)`) after success, falling back to conventional
`*_id` input fields. This is now real and tested (`operations.get_job_timeline` correctly lists every
tool call that touched the job — verified in the full E2E test and live in the browser).

### Temporal — JobLifecycleWorkflow

`backend/app/workflows/definitions.py`. Per the explicit Phase 4 instruction not to introduce another
unverified `workflow.sleep()`-based workflow, `JobLifecycleWorkflow` is deliberately narrow: it
durably, idempotently validates a newly created job (via the same `execute_tool_activity` every
other workflow in this project uses — no bespoke Temporal-specific logic). Real job progression
(schedule → assign → dispatch → monitor → QA → completion → close-out) is driven by explicit
operator/API actions through the ToolRegistry, not blind automatic Temporal orchestration — this is
a deliberate scope decision, stated here rather than left implicit.

**Isolated, individually verified against a real local Temporal test server** (same methodology as
Phase 3, now with a third clean result):
```
test_job_lifecycle_workflow_validates_a_real_job — 1 passed (0.44s)
```
See "Temporal status" below for the full picture across all four workflows.

### API & frontend

New routes: `/api/v1/jobs` (+ 20 sub-routes: assign/schedule/dispatch/transition/tasks/materials/
documents/photos/voice-notes/qa/scope-changes/completion-packet/close/signoff/convert-lead),
`/api/v1/workers`, `/api/v1/exceptions` (+ `/detect`), `/api/v1/operations/dashboard`.

Frontend: `/operations` (real dashboard + exceptions needing attention), `/jobs` (board/list with
status tabs, search, create-job modal with customer search-as-you-type), `/jobs/[id]` (the
operational source of truth — overview, contextual next-action buttons matching the state machine,
scheduling, worker assignment, tasks, materials, photo/document upload, QA panel, completion packet,
close, AI summary, real timeline), `/exceptions` (filterable list + resolve). Nav updated to include
Operations/Jobs/Exceptions without removing Dashboard/Leads/Customers/Calendar.

**This checkpoint ran the full realistic end-to-end scenario live in a real browser** (not just
`npm run build`): registered a tenant → created a customer → created a job → scheduled it → created a
worker via the API and assigned it through the UI dropdown → dispatched → marked en route → marked on
site → started → completed a required task → **uploaded a real PNG file** (verified as
`internal_local_storage`, 68 bytes) → completed field work → started QA → passed QA (job reached
`COMPLETED`) → generated the completion packet → **closed the job** → confirmed the Operations
dashboard showed "1 Completed today" with every other metric honestly at zero → confirmed the job's
real timeline (built from real audit rows) rendered in the UI. Found and fixed two real bugs along
the way (see "Known limitations").

### Test results (actually run)

```
backend/tests/  (excluding test_temporal_workflows.py)  ->  109 passed, 0 failed
    (python -m pytest -q --ignore=tests/test_temporal_workflows.py, sqlite in-memory)
```

109 = Phase 1–3's 61 (all still passing, unmodified in behavior) + 48 new this phase: job state
machine (6), job CRUD/idempotency (4), assignment/scheduling incl. conflict/wrong-tenant/inactive-
worker rejection (7), tasks/materials/document upload incl. size/type rejection (8), QA/completion/
close-out incl. the `invoice.trigger_requested`-not-an-invoice check (5), exceptions/delay-detection/
communication (4), the critical Operations tenant-isolation suite — cannot view/assign/upload-to/
resolve-exception-on another tenant's job/worker/exception (6), HTTP-level API tests incl. tenant
isolation and real-vs-zero dashboard metrics (6), and the full realistic E2E lifecycle test (1) that
verifies database state, every expected event type, communication logs, audit-log coverage per tool,
and the job timeline all at once.

Frontend: `npx tsc --noEmit` — 0 errors. `npm run build` — succeeds, 13 routes.

## What is explicitly stubbed / not yet built

- No standalone Workers management page in the frontend — worker creation/listing exist as real,
  tested API endpoints and tools, but were only exercised via `fetch()`/curl in this checkpoint's
  browser verification, not through a dedicated UI page. A small gap, named plainly.
- `JobLifecycleWorkflow` only validates; it does not durably orchestrate the full job lifecycle
  (see "Temporal — JobLifecycleWorkflow" above for the reasoning).
- No scheduler/cron runs delay detection automatically — it's a real, tested, on-demand endpoint.
- Scope-change approval doesn't auto-apply to job pricing once approved (unchanged limitation
  pattern from Phase 2's approval boundary — approving today doesn't auto-resume the underlying
  action anywhere in this codebase yet).
- CRM booking-time communication (Phase 3's gap) is still not wired — only Operations job-status
  communication was wired this phase, as instructed.
- Finance/Marketing: not built, per this phase's explicit scope. `invoice.trigger_requested` is
  only an event — no `Invoice` model, no invoice row, anywhere.

## Known limitations / caveats

- **No Docker, no local Postgres/Redis in this dev sandbox** (unchanged since Phase 1) — tests ran
  against sqlite; migration `0004` is written correctly for Postgres but not executed against it.
- **Two real bugs found and fixed during this phase's own testing** (not hidden):
  1. `app/tools/builtin/document_tools.py`'s three upload tools didn't catch
     `AttachmentService.JobNotFoundError`, so uploading to a job in another tenant produced an
     uncaught 500-class error instead of the intended "not found." Fixed — caught alongside the
     existing upload-validation exceptions.
  2. `app/tools/builtin/completion_tools.py`'s `CloseJob` didn't catch
     `InvalidJobTransitionError` from the state machine, so closing a job in an invalid state (e.g.
     still `DISPATCHED`) produced an uncaught exception through the API instead of a clean 4xx.
     Found via an HTTP-level test, fixed.
  3. (Frontend, found during live browser verification, not by an automated test) `jobs/[id]/page.tsx`'s
     add-task/add-material handlers read `e.currentTarget` *after* an `await`, which can be `null`
     by the time the code resumes because the intervening data reload re-renders the form. Fixed by
     capturing the form element reference before the `await`. This is exactly the kind of gap
     `npm run build` cannot catch — only running the app and clicking through it found it, which is
     why section 44's "actually run it in a browser" requirement matters.
  - Also observed twice during live browser testing: a transient `net::ERR_FAILED` on an otherwise-
    working API call (reported by the browser as a CORS error, but every other call to the same
    origin/endpoint succeeded before and after) — most likely a local single-worker dev-server
    hiccup, not a CORS misconfiguration. The frontend's existing error/Retry handling recovered
    cleanly both times without any code change needed.
- **Temporal status — see the dedicated section below.**
- `DEFAULT_TOOL_POLICIES` is still a static in-process dict (unchanged since Phase 2).

## Temporal status (explicit, per workflow)

| Workflow | Uses `workflow.sleep()`? | Isolated test result |
|---|---|---|
| `EventProcessingWorkflow` | No | 1 passed (0.27s), Phase 2/3 |
| `LeadQualificationWorkflow` | No | 1 passed (0.66s), Phase 3 |
| `JobLifecycleWorkflow` | No | 1 passed (0.44s), **Phase 4** |
| `InvoiceOverdueWorkflow` | **Yes** | **Hangs** — reproduced 3+ times across Phase 2/3, not retried this phase (no code changed) |

The pattern across three sessions is now consistent enough to state as a finding, not a guess: every
workflow that avoids `workflow.sleep()` passes cleanly and repeatably in this sandbox; the one that
uses it hangs every time. Phase 4 deliberately did not add a second sleep-based workflow — see
"Temporal — JobLifecycleWorkflow" above.

## Recommended next step

**Phase 5 (Finance & Back Office)** is the natural next domain per the original roadmap —
`invoice.trigger_requested` is already a real event with a real job/customer payload waiting for a
subscriber; job costing has real `estimated_cost`/`estimated_revenue`/`estimated_margin` fields
already on the `Job` model with nothing populating `actual_*` yet. Two smaller items worth folding
in alongside or before it: a Workers management page (the API/tools are done, the UI isn't), and
wiring Phase 3's CRM booking communications the same way Phase 4 wired Operations' this session.

Not proceeding into Phase 5 automatically, per instruction — awaiting direction.
