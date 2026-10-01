# Phase 17B-2R — Tenant DB Session Inventory (living document)

Status: **FILE-LEVEL WORK COMPLETE** (round 12) — every file in the
131-file inventory now has `set_tenant_context` call count == its own
session-open site count, OR its mismatch is one of the 9 explicitly
documented/tested correctly-excluded classifications below. See "Round 12
summary" at the end of this document. Full regression suite run pending
write-up in the final `PHASE_17B2R_COMPLETE_TENANT_CONTEXT_PROPAGATION_LOG.md`
deliverable.

Historical status note (rounds 1-11): **IN PROGRESS**, first-pass file-level classification complete; per-site
line-level detail is filled in as each file is actually fixed and tested
(see the "17B-2R STATUS" column — "FIXED+TESTED" rows have full per-site
detail below the summary table; every other row is first-pass only, per
the task's explicit allowance: "You don't need to hand-trace every
indirect wrapper perfectly on the first pass").

Mechanism used throughout: `app.db.session.set_tenant_context` (the one
authoritative Phase 0 mechanism) called on each independently-opened
session, immediately after `async with self._session_factory() as
session:` (or equivalent), before any tenant-owned query on that session.
No competing tenant-context mechanism has been introduced.

## Running tally

| Metric | Count |
|---|---|
| Files with `session_factory()`/`async_session_maker()`/`AsyncSession(` sites (excl. tests) | 131 |
| Raw call-site grep hits | 530 |
| Files fully fixed + real-Postgres tested this phase (17B-2R) | 76 (rounds 2-9: 47 files; round 10: 14 files; round 11: `ai_invocation_log_service.py`, `ai_next_action_service.py`, `ai_qualification_service.py`, `attachment_service.py`, `cash_forecast_service.py`, `collection_service.py`, `conversion_service.py`, `delay_detection_service.py`, `operations_communication_service.py`, `openai_realtime_voice_service.py`, `owner_activity_service.py`, `owner_attention_service.py`, `voice_conversation_service.py`, `quickbooks_sync_service.py`, `quickbooks_payment_sync_service.py`, `quickbooks_refund_sync_service.py` — 15 files) |
| Files re-verified as ALREADY fully correct from Phase 17B-2 (no changes needed) | 19 of the original 22 (see "Round 4" section below) |
| Files classified GLOBAL/no-fix-needed this phase | `integration_catalog_service.py` (round 9) + `website_generation_service.py` (round 11, `_resolve_vertical_data_sources` reads only the GLOBAL `VerticalExtension` catalog) — 2 files |
| Files inspected and found ALREADY fully instrumented (no change needed) | `app/workflows/activities.py` (round 11 — both session sites in `resume_automation_execution_activity` already called `set_tenant_context`) |
| Files still fully pending (genuinely untouched) | 33 |
| UNKNOWN (ambiguous tenant semantics, not yet resolved) | 0 so far (none hit yet — will update as audit proceeds) |

## Files already touched by Phase 17B-2 (git diff at session start) — RE-VERIFIED this round (Round 4)

Re-verification method: for each of the 22 files, compared the raw
session-open-site grep count against the count of actual
`set_tenant_context(` calls in the same file, then manually inspected any
mismatch to determine whether it was a real gap or a correctly-excluded
site (GLOBAL/SHARED data or an already-documented CROSS_TENANT_SYSTEM
sweep).

**19 of 22 files: fully correct, zero gaps, no changes needed.** Site
count == `set_tenant_context` call count in every case:
`agent_trigger_handlers.py` (3/3), `automation_handlers.py` (2/2),
`crm_handlers.py` (1/1), `finance_handlers.py` (2/2), `handlers.py` (1/1),
`marketing_handlers.py` (4/4), `notification_handlers.py` (3/3),
`operations_handlers.py` (1/1), `retention_handlers.py` (4/4),
`mcp/protocol.py` (1/1), `agent_execution_service.py` (16/16),
`agent_reasoning_service.py` (13/13), `agent_recovery_service.py` (5/5),
`agent_trigger_service.py` (3/3), `lead_service.py` (1/1),
`website_service.py` (14/14), `tools/registry.py` (5/5),
`public_leads.py` (1/1), `discovery_extraction_service.py` (0 sites — this
file never opens its own session at all, just holds and forwards a
`session_factory` reference to another service; trivially clean).

**2 files: apparent mismatch, investigated, confirmed correctly-excluded
(not a gap)**:
- `webhooks.py` (6 sites, 5 `set_tenant_context` calls): the 1 uncovered
  site is the Stripe dedup lookup against `WebhookEvent`, a table
  deliberately NOT `TenantScopedMixin` (see
  `app/models/integration.py`'s own docstring — tenant isn't known until
  AFTER signature verification resolves it from the payload). Classified
  **C. GLOBAL/SHARED DATA** — correctly excluded, not a gap.
- `bus.py` (4 sites, 3 `set_tenant_context` calls): the 1 uncovered site
  (`_requeue_stuck_events`) already carries Phase 17B-2's own inline
  comment explicitly classifying it CROSS_TENANT_SYSTEM and flagging it
  for Phase 17B-3 — exactly the pattern this round's own new sweeps
  (automation, morning_brief) follow. Correctly excluded, not a gap.

**1 file: real gap found and fixed this round**:
- `business_discovery_service.py` (6 sites, 0 `set_tenant_context` calls
  before this round): this file's presence in the Phase 17B-2 git diff was
  actually **Phase 16B's** discovery-fallback work (unrelated to tenant
  context) — it was never actually touched by 17B-2's own tenant-context
  instrumentation, despite being in the "already touched" bucket by a
  naive git-diff read. Fixed this round: 6/6 sites now call
  `set_tenant_context` using `tenant_id`/`turn.tenant_id` (verified in
  scope at every site). Tested in
  `test_tenant_context_business_discovery_service_phase17b2r.py` (2 tests,
  passing). Regression: 64/64 discovery-tagged tests pass.

This re-verification is a genuinely important finding: it shows a
file merely appearing in a prior phase's git diff is NOT reliable evidence
that tenant-context work was done there — `business_discovery_service.py`
looked "already handled" from the diff alone but wasn't. The lesson
applied going forward: every file's actual `set_tenant_context` call count
must be checked against its own site count, never assumed from diff
presence.

## Priority order for 17B-2R fixes (per task §9-14 + coordinator direction)

1. `automation_service.py` (23 sites) — **FIXED + TESTED this round**
2. `notification_service.py` (10 sites) — next
3. `invoice_service.py` (8 sites)
4. `attribution_service.py` (5 sites)
5. `qualification_service.py` (1 site)
6. `enrichment_service.py` (1 site)
7. `morning_brief_service.py` (2 sites — likely mixed tenant-scoped/cross-tenant sweep, needs care)
8. `medical_tourism_service.py` (16 sites)
9. Representative Tool implementations (`app/tools/builtin/*`, 13 files)
10. Remaining ~100 services, by descending site count (see table below)

## File-level first pass (all 131 files, sorted by session-open site count)

Legend — TENANT SOURCE: `param` = tenant_id is an explicit method parameter
(strong signal of TENANT-SCOPED, source = caller-trusted); `mixed` = some
methods take tenant_id, others look like cross-tenant sweeps; `TBD` = not
yet traced this round. 17B-2R STATUS: `FIXED+TESTED` / `PENDING`.

| # | File | Sites | Tenant source (first pass) | 17B-2 entrypoint touch? | 17B-2R status |
|---|---|---|---|---|---|
| 1 | backend/app/services/automation_service.py | 23 | param (tenant_id on every public method; `check_and_dispatch_scheduled` mixed — optional tenant_id, None = genuine cross-tenant sweep) | no | **FIXED+TESTED** |
| 2 | backend/app/services/medical_tourism_service.py | 16 | param (verified — 15 class methods + 1 module-level public-website data-provider callback, all take tenant_id) | no | **FIXED+TESTED** |
| 3 | backend/app/services/agent_execution_service.py | 16 | param | yes (17B-2) | **VERIFIED COMPLETE** (16/16 sites, re-confirmed round 4) |
| 4 | backend/app/services/website_service.py | 14 | param | yes (17B-2) | **VERIFIED COMPLETE** (14/14 sites, re-confirmed round 4) |
| 5 | backend/app/services/agent_service.py | 13 | param (verified — every public method takes tenant_id) | no | **FIXED+TESTED** |
| 6 | backend/app/services/agent_reasoning_service.py | 13 | param | yes (17B-2) | **VERIFIED COMPLETE** (13/13 sites, re-confirmed round 4) |
| 7 | backend/app/services/retention_service.py | 12 | param (verified) | no | **FIXED+TESTED** |
| 8 | backend/app/services/business_blueprint_service.py | 12 | param (verified) | no | **FIXED+TESTED** |
| 9 | backend/app/tools/builtin/crm_tools.py | 11 | mixed — CreateLead/BulkImportLeads delegate entirely to `LeadService.create_lead` (already fixed, zero own session sites in those 2 Tools); the other 9 Tools (GetLead, UpdateLead, SearchLeads, CreateCustomer, BulkImportCustomers, GetCustomer, UpdateCustomer, SearchCustomers, GetCustomerTimeline+notes) each open their own session directly, all verified using `context.tenant_id` | no | **FIXED+TESTED** |
| 10 | backend/app/services/referral_service.py | 11 | param (verified) | no | **FIXED+TESTED** |
| 11 | backend/app/services/payment_service.py | 11 | param (verified — includes Stripe-linked decide_refund/reconcile_external_refund; no auth/signature logic touched, only added context using the already-trusted tenant_id parameter) | no | **FIXED+TESTED** |
| 12 | backend/app/services/business_journey_service.py | 11 | param (verified — includes the special `lock_session` advisory-lock session in start_journey, a distinct pattern from `async with self._session_factory()`) | no | **FIXED+TESTED** |
| 13 | backend/app/services/notification_service.py | 10 | param (verified — every public method takes tenant_id) | no | **FIXED+TESTED** |
| 14 | backend/app/services/company_memory_service.py | 10 | param (verified) | no | **FIXED+TESTED** |
| 15 | backend/app/services/recommendation_service.py | 9 | mixed — 7 tenant-scoped sites fixed; 2 sites only read the GLOBAL IntegrationProviderCatalog/VerticalExtension catalog tables, correctly excluded | no | **FIXED+TESTED** |
| 16 | backend/app/services/quote_service.py | 8 | param (verified — including get_for_public_view, public quote-view-token boundary per §21) | no | **FIXED+TESTED** |
| 17 | backend/app/services/invoice_service.py | 8 | param (verified — every public method takes tenant_id) | no | **FIXED+TESTED** |
| 18 | backend/app/services/insight_service.py | 8 | param (verified — all 8 read-only snapshot methods) | no | **FIXED+TESTED** |
| 19 | backend/app/services/content_service.py | 8 | param (verified — all 8 sites) | no | **FIXED+TESTED** |
| 20 | backend/app/services/voice_call_service.py | 7 | param (verified — all 7 sites, end_call delegates to update_call) | no | **FIXED+TESTED** |
| 21 | backend/app/services/vertical_extension_service.py | 7 | mixed — 4 sites on `VerticalExtension` (global platform catalog, no tenant_id column, correctly NEVER set context) + 3 sites on `OrganizationVerticalExtension` (genuinely tenant-scoped, set context) | no | **FIXED+TESTED** |
| 22 | backend/app/services/team_service.py | 7 | mixed — 5 sites take tenant_id directly; `preview_invite`/`accept_invite` derive tenant_id from this codebase's own signed invite JWT, verified inside `_resolve_pending_invite` BEFORE context is set (never a client-supplied claim) | no | **FIXED+TESTED** |
| 23 | backend/app/services/mcp_service.py | 7 | mixed — 6 sites take tenant_id directly (McpExposureService x3, McpCredentialService x3); `McpCredentialService.authenticate` (the 7th) is the tenant-resolution step itself — correctly excluded, classified GLOBAL/authentication-boundary like the Stripe webhook dedup lookup | no | **FIXED+TESTED** |
| 24 | backend/app/services/license_service.py | 7 | param (verified — all 7 sites, including detect_expiring's nested per-license loop sessions) | no | **FIXED+TESTED** |
| 25 | backend/app/services/job_transition_service.py | 7 | param (verified — dispatch/start/complete/cancel delegate to _apply_transition, no separate sites) | no | **FIXED+TESTED** |
| 26 | backend/app/services/contract_service.py | 7 | param (verified — including get_for_public_view, whose tenant_id comes from the documented public-token URL boundary per §21) | no | **FIXED+TESTED** |
| 27 | backend/app/services/approval_execution_service.py | 7 | param (verified — security-sensitive Agent approval CAS flow; no auth/state-transition logic touched, only set_tenant_context added using the already-trusted tenant_id) | no | **FIXED+TESTED** |
| 28 | backend/app/services/outbound_service.py | 6 | param (verified — all 6 sites) | no | **FIXED+TESTED** |
| 29 | backend/app/services/business_discovery_service.py | 6 | param (16B work) | yes (16B, not tenant-context) | PENDING (tenant-context re-verify) |
| 30 | backend/app/api/v1/webhooks.py | 6 | verified/webhook-metadata | yes (17B-2) | PENDING (full-file re-verify) |
| 31 | backend/app/tools/registry.py | 5 | delegates to ToolRegistry-level context (17B-2) | yes (17B-2) | PENDING (per-tool re-verify, see §9 of task) |
| 32 | backend/app/tools/builtin/event_tools.py | 5 | TBD | no | PENDING |
| 33 | backend/app/services/warranty_service.py | 5 | param (verified — all 5 sites) | no | **FIXED+TESTED** |
| 34 | backend/app/services/vendor_service.py | 5 | param (verified — all 5 sites) | no | **FIXED+TESTED** |
| 35 | backend/app/services/policy_service.py | 5 | param (verified — all 5 sites) | no | **FIXED+TESTED** |
| 36 | backend/app/services/nurture_service.py | 5 | param (verified — all 5 sites) | no | **FIXED+TESTED** |
| 37 | backend/app/services/knowledge_service.py | 5 | param (verified — all 5 sites) | no | **FIXED+TESTED** |
| 38 | backend/app/services/integration_connection_service.py | 5 | param (verified — all 5 sites) | no | **FIXED+TESTED** |
| 39 | backend/app/services/campaign_service.py | 5 | param (verified — all 5 sites) | no | **FIXED+TESTED** |
| 40 | backend/app/services/billing_service.py | 5 | mixed — 3 sites take tenant_id/org.id directly; `apply_subscription_updated`/`apply_subscription_deleted` (2 sites) resolve tenant identity from a Stripe-verified subscription_id lookup, context set AFTER resolution (resolve-then-stamp pattern, same as billing.py's own webhook signature gate upstream) | no | **FIXED+TESTED** |
| 41 | backend/app/services/attribution_service.py | 5 | param (verified — every public method takes tenant_id) | no | **FIXED+TESTED** |
| 42 | backend/app/services/agent_recovery_service.py | 5 | param | yes (17B-2) | PENDING (full-file re-verify) |
| 43 | backend/app/api/v1/jobs.py | 5 | authenticated router — `current_user.tenant_id` (JWT), never client-supplied. `_owned_job_or_404` + `list_job_tasks`/`list_job_materials`/`list_job_attachments`/`download_job_attachment` each open their own independent `async_session_maker()` session (a different session than any FastAPI-DI session) | no | **FIXED+TESTED** |
| 44 | backend/app/services/qualification_service.py | 1 | param (verified) | no | **FIXED+TESTED** |
| 45 | backend/app/services/enrichment_service.py | 1 | param (verified) | no | **FIXED+TESTED** |
| 46 | backend/app/services/morning_brief_service.py | 2 | mixed — `generate` is param/tenant-scoped (fixed); `check_and_generate_scheduled`'s own sweep session is genuine CROSS_TENANT_SYSTEM (no tenant_id in scope, explicitly flagged for 17B-3, NOT faked) | no | **FIXED+TESTED** |
| 41 | backend/app/services/seo_service.py | 4 | param (verified — all 4 sites) | no | **FIXED+TESTED** |
| 42 | backend/app/services/retention_campaign_service.py | 4 | param (verified — all 4 sites) | no | **FIXED+TESTED** |
| 43 | backend/app/services/local_service.py | 4 | param (verified — all 4 sites) | no | **FIXED+TESTED** |
| 44 | backend/app/services/integration_catalog_service.py | 4 | GLOBAL — every session-open site operates only on `IntegrationProviderCatalog`, deliberately NOT tenant-scoped (see app/models/integration_catalog.py's own docstring); 0 of 4 sites need context | no | **CLASSIFIED, NO FIX NEEDED (verified with test)** |
| 45 | backend/app/services/google_calendar_sync_service.py | 4 | param (verified — including `sync_appointment`'s own PostgreSQL-advisory-locked session) | no | **FIXED+TESTED** |
| 46 | backend/app/services/ar_service.py | 4 | param (verified — all 4 sites) | no | **FIXED+TESTED** |
| 47 | backend/app/services/adjustments_service.py | 4 | param (verified — all 4 sites, both credit-note and write-off request/decide) | no | **FIXED+TESTED** |
| 48 | backend/app/services/review_service.py | 3 | param (verified — all 3 sites) | no | **FIXED+TESTED** |
| 49 | backend/app/services/reactivation_service.py | 3 | param (verified — all 3 sites) | no | **FIXED+TESTED** |
| 50 | backend/app/services/quickbooks_import_service.py | 3 | param (verified — all 3 sites) | no | **FIXED+TESTED** |
| 51 | backend/app/services/qa_service.py | 3 | param (verified — all 3 sites) | no | **FIXED+TESTED** |
| 52 | backend/app/services/knowledge_retrieval_service.py | 3 | param (verified — all 3 sites) | no | **FIXED+TESTED** |
| 53 | backend/app/services/job_service.py | 3 | param (verified — including the `fresh_session`-named IntegrityError-recovery site a naive `as session:` grep would have missed) | no | **FIXED+TESTED** |
| 54 | backend/app/services/job_costing_service.py | 3 | param (verified — all 3 sites) | no | **FIXED+TESTED** |
| 55 | backend/app/services/quote_deposit_service.py | 2 | param (verified — Stripe-linked, no auth/signature logic touched) | no | **FIXED+TESTED** |
| 56 | backend/app/services/task_service.py | 2 | param (verified — all 2 sites) | no | **FIXED+TESTED** |
| 57 | backend/app/services/material_service.py | 2 | param (verified — all 2 sites) | no | **FIXED+TESTED** |
| 58 | backend/app/services/exception_service.py | 2 | param (verified — widely-depended-on by many already-fixed services this phase) | no | **FIXED+TESTED** |
| 59 | backend/app/services/completion_service.py | 2 | param (verified — all 2 sites) | no | **FIXED+TESTED** |
| 60 | backend/app/services/scope_change_service.py | 1 | param (verified) | no | **FIXED+TESTED** |
| 61 | backend/app/services/signoff_service.py | 1 | param (verified) | no | **FIXED+TESTED** |
| 62-131 | (remaining ~70 files, 1-4 sites each — see raw grep output; full list: agent_trigger_service, ai_invocation_log_service, ai_next_action_service, ai_qualification_service, appointments.py, attachment_service, automations.py, billing.py (route), calendar/internal_test_adapter, cash_forecast_service, collection_service, communications/*_adapter, conversion_service, crm.py, dashboard.py, delay_detection_service, events.py, invoice_delivery/internal_test_adapter, job_summary_tool, job_tools, lead_service, main.py, marketing.py, marketplace_webhooks.py, mcp_admin.py, morning_brief.py (API route), morning_brief_tools, operations.py, operations_communication_service, openai_realtime_voice_service, organization_tools, owner_activity_service, owner_attention_service, public_leads.py, public_quotes.py, quickbooks_*_service (4 remaining files: sync/payment_sync/refund_sync/pull-already-done), quote_tools, retention.py, stripe_tools, voice_conversation_service, website_generation_service, workflows/activities.py, worker_tools) | 1-4 each | TBD | mixed (some yes via handler files) | PENDING |

## §7 Service Session Contract — applied to automation_service.py this round

Verified concretely (not assumed): every `AutomationService` public method
takes `tenant_id: uuid.UUID` as an explicit parameter, and every one of its
23 `self._session_factory()` call sites now calls
`await set_tenant_context(session, tenant_id)` as the first statement
inside the `async with` block, using the SAME session the method's
tenant-scoped queries run on (never a different session than the one
queried). One method, `check_and_dispatch_scheduled`, is genuinely mixed:
called with a concrete `tenant_id` from the manual test-tick HTTP endpoint
(single-tenant-scoped — context correctly set), or with `tenant_id=None`
from the real background worker tick (genuine CROSS_TENANT_SYSTEM sweep —
`set_tenant_context(session, None)` is a documented no-op per
`app/db/session.py`, never faked with an arbitrary tenant). This dual
nature is pre-existing in the code (see the method's own docstring) and is
NOT a new design introduced by this phase — this phase only adds the
context call, without changing when the sweep fires or what it scans, and
explicitly flags the `tenant_id=None` branch as Phase 17B-3 scope (see the
new test `test_check_and_dispatch_scheduled_global_sweep_is_not_faked` in
`backend/tests/test_tenant_context_automation_service_phase17b2r.py`).

## Cross-tenant system operations flagged so far (not fixed, explicitly deferred to 17B-3)

- `AutomationService.check_and_dispatch_scheduled(tenant_id=None)` —
  background worker's own scheduled-dispatch tick, scans all tenants'
  ENABLED+SCHEDULE automations. Explicitly classified CROSS_TENANT_SYSTEM.
  Test coverage: `test_check_and_dispatch_scheduled_global_sweep_is_not_faked`.
- `MorningBriefService.check_and_generate_scheduled`'s own session (the
  `select(Organization).where(Organization.morning_brief_enabled.is_(True))`
  scan) — genuine CROSS_TENANT_SYSTEM sweep across every enabled org, no
  single tenant_id in scope. Explicitly flagged in the code itself now (a
  docstring + inline comment referencing Phase 17B-3, added this round) and
  NOT given a fabricated tenant context. Each due tenant's own
  `self.generate(tenant_id, ...)` call — a SEPARATE, correctly tenant-scoped
  session — is where that tenant's actual work and context-setting happens.
  Test coverage: `test_scheduled_sweep_is_cross_tenant_and_never_fabricates_context`.
- Further cross-tenant sweeps are still expected in `retention_service.py` /
  `reactivation_service.py` (not yet traced — will be classified when those
  files are audited in a future round).

## Round 2 summary (this round)

Fixed, tested (new dedicated real-Postgres test files, all passing), and
regression-checked against each service's own existing test suite (all
green, no failures introduced):

1. `automation_service.py` (23 sites) — `test_tenant_context_automation_service_phase17b2r.py` (5 tests), regression: 132/132 automation-tagged tests pass.
2. `notification_service.py` (10 sites) — `test_tenant_context_notification_service_phase17b2r.py` (4 tests, incl. an explicit A/B/A/B pool-reuse proof), regression: 32/32 notification-tagged tests pass.
3. `invoice_service.py` (8 sites) — `test_tenant_context_invoice_service_phase17b2r.py` (3 tests, incl. cross-tenant void-denial and A/B pool reuse), regression: 100 passed/1 skipped invoice-tagged tests.
4. `attribution_service.py` (5 sites) — `test_tenant_context_attribution_service_phase17b2r.py` (3 tests), regression: 8/8 attribution-tagged tests pass.

Total new tests this round: **15**, all passing against a real, disposable
PostgreSQL 16 instance (pgserver-backed, bootstrapped this round — see
"PostgreSQL bootstrap notes" below). Total session-open sites fixed this
round: **46** (23+10+8+5). No RLS policy touched. No commit, no push.

## PostgreSQL bootstrap notes (for the next resumed round)

`pgserver`'s own `psql()` helper is broken in this repo's checkout because
it shells out with `shell=True` and an unquoted path — and this project's
absolute path contains a space (`.../Desktop/Klaros AI/...`), which the
prior phases' own docs don't mention hitting (their sandboxes presumably
didn't have a space in the path). Worked around this round by calling the
`psql` binary directly via `subprocess.run([...])` (list args, no shell)
instead of `srv.psql(...)`. A disposable instance was started in the
session scratchpad (`pgdata` dir), the `klaros` superuser role + `klaros`
database + `pgvector` extension created, `alembic upgrade head` run clean
(52 revisions, head `0051`), and `DATABASE_URL`/`DATABASE_MIGRATION_URL`
pointed at it via a sourced env file. This instance is still running in
the background for this session; the next resumed round should check if
it's still alive (`ps aux | grep start_pg.py`) before re-bootstrapping.

## Round 3 summary (this round)

Fixed, tested (new dedicated real-Postgres test files, all passing), and
regression-checked:

5. `qualification_service.py` (1 site) + `enrichment_service.py` (1 site,
   called from inside `qualify()`, a SEPARATE session) —
   `test_tenant_context_qualification_enrichment_phase17b2r.py` (3 tests,
   proves BOTH independently-opened sessions set context, not just one),
   regression: 36/36 qualif./enrich.-tagged tests pass, plus 69/69
   lead-tagged tests pass (crm_handlers/workflows call chain unaffected).
6. `morning_brief_service.py` (2 sites, genuinely mixed) —
   `test_tenant_context_morning_brief_service_phase17b2r.py` (3 tests,
   including a test that proves the sweep processes 2 tenants in one pass
   while never fabricating context for its own un-scoped session), added
   an explicit CROSS_TENANT_SYSTEM code comment/docstring on
   `check_and_generate_scheduled`'s own session referencing Phase 17B-3.
   Regression: 33/33 morning_brief-tagged tests pass.
7. `medical_tourism_service.py` (16 sites — 15 class-method sessions + 1
   module-level session inside the public Website data-provider callback
   `_provide_website_provider_directory`, a real gap distinct from the
   class's own sites) —
   `test_tenant_context_medical_tourism_service_phase17b2r.py` (3 tests,
   including one specifically for the public-website data-provider path
   per §21's Public/Authenticated/Internal distinction). Regression: 49/49
   medical_tourism-tagged tests pass, 81/81 website-tagged tests pass.

New tests this round: **9**, all passing against the same real Postgres
instance. Session-open sites fixed this round: **20** (1 qualification_service.py
+ 1 enrichment_service.py + 2 morning_brief_service.py + 16 medical_tourism_service.py).

**Running total across rounds 2+3: 8 services fully fixed, 66 session-open
sites closed, 24 new tests, zero regressions in any touched service's own
test suite.**

## Round 4 summary (this round)

1. **`crm_tools.py`** (11 sites — the representative Tool implementation):
   fixed all 11 direct-session Tools (GetLead, UpdateLead, SearchLeads,
   CreateCustomer, BulkImportCustomers, GetCustomer, UpdateCustomer,
   SearchCustomers, GetCustomerTimeline, plus 2 note-related Tools), all
   using `context.tenant_id` (ToolRegistry-validated before `execute()`
   runs, never a raw client value). Confirmed 2 other Tools in the same
   file (CreateLead, BulkImportLeads) have ZERO session sites of their
   own — they delegate entirely to `LeadService.create_lead`, already
   fixed in round 2, so no separate fix was needed for those two.
   `test_tenant_context_crm_tools_phase17b2r.py` (3 tests, driven through
   the real `ToolRegistry.execute()` entrypoint, not the Tool class
   directly — incidentally re-confirms MCP/Agent call chains into the Tool
   layer preserve tenant identity per task §22). Regression: 27/27
   crm-tagged, 65/65 customer/tool_registry-tagged tests pass.
2. **`app/api/v1/jobs.py`** (5 sites — the representative authenticated
   HTTP route): fixed `_owned_job_or_404` + 4 route handlers that each
   open their own independent `async_session_maker()` session (distinct
   from any FastAPI-DI session), using `current_user.tenant_id` from the
   authenticated JWT. `test_tenant_context_jobs_api_phase17b2r.py` (3
   tests, driven through the REAL HTTP client — register, real JWT, real
   endpoint — not calling the route function directly). Regression: 65/65
   job-tagged tests pass.
3. **Re-verified all 22 files Phase 17B-2 partially touched** against
   their full site counts (see the section above) — found 19 already
   fully correct, 2 with correctly-excluded (not gaps) mismatches, and 1
   real gap (`business_discovery_service.py`, fixed this round, 6 sites).

New tests this round: **8** (3+3+2), all passing. Session-open sites fixed
this round: **22** (11+5+6). Zero regressions in any touched area.

**Running total across rounds 2-4: 11 files newly fixed by 17B-2R (88
sites), 19 files re-confirmed already correct from 17B-2, 2 files
re-confirmed correctly-excluded, 1 file's own 0-site case confirmed
trivially clean. 32 tests total, all passing, zero regressions anywhere.**

## Honest assessment: how much of the remaining ~99 files needs individual work?

The coordinator asked whether raw file count overstates remaining work,
since many files might be transitively covered by the 8 services + 1 tool
file already fixed. Checked this directly rather than assuming:

**Short answer: no, the file count does NOT meaningfully shrink via
transitivity — every one of the 131 files in this inventory has at least
one session-open site that is its OWN, not inherited from a callee.**
That's mechanically guaranteed by how the inventory was built (grepped
directly for `session_factory()`/`async_session_maker()`/`AsyncSession(`
in each file) — a file with zero such sites of its own was never in the
131-file list to begin with, so "transitively covered" files are already
excluded, not hiding inside the remaining count.

**What DOES reduce genuinely, and is worth knowing**: within a single Tool
file, not every individual `Tool.execute()` needs its own fix — some
delegate 100% to an already-fixed service (like `crm_tools.py`'s
CreateLead/BulkImportLeads calling `LeadService.create_lead`) and have
zero session sites of their own. Sampled the 13 other still-pending
`app/tools/builtin/*.py` files' TOTAL site counts (not zero for any of
them — `job_tools.py` 4, `event_tools.py` 5, `morning_brief_tools.py` 3,
`worker_tools.py` 3, `approval_tools.py` 3, `invoice_tools.py` 2,
`organization_tools.py` 2, `audit_tools.py` 2, `quote_tools.py` 1,
`notification_tools.py` 1, `stripe_tools.py` 1,
`automation_policy_tools.py` 1, `job_summary_tool.py` 1 — 29 sites total
across 13 files), then spot-checked `job_tools.py` specifically: of its
~10 Tool classes, CreateJob/AssignJob/UnassignJob/ScheduleJob/
RescheduleJob/DispatchJob/UpdateJobStatus (7 of 10) delegate entirely to
`self._job_service.*` (i.e., `job_service.py`, NOT yet fixed) with zero
session sites of their own — only GetJob/UpdateJob/SearchJobs open direct
sessions. **This means the true bottleneck is the underlying ~60 remaining
service files, not the tool files layered on top of them** — once a
service like `job_service.py` (3 sites) is fixed, the job_tools.py Tools
that delegate to it need no separate fix at all, and only job_tools.py's
own 3 direct-session Tools need their own pass. Net effect: the tool-layer
work is smaller than its raw file count suggests (many tool files will
need only 1-3 of their own sites fixed once their underlying service is
done), but the ~60-70 remaining SERVICE files each still need a full,
individual, real fix — there is no shortcut there. Realistic estimate:
~99 remaining files, but perhaps 15-20 fewer person-rounds of tool-layer
work than a flat per-file count would suggest, concentrated instead in
finishing the service layer first (`agent_service.py` 13,
`retention_service.py` 12, `business_blueprint_service.py` 12,
`referral_service.py` 11, `payment_service.py` 11,
`business_journey_service.py` 11, `company_memory_service.py` 10, then
the rest downward).

## Round 5 summary (this round)

Fixed and tested all 7 services the coordinator listed in one round,
same standard as every prior round — real-Postgres test proving the GUC
is set on the same session, at least one cross-tenant-denial test per
service, per-service regression check:

1. `agent_service.py` (13 sites) — `test_tenant_context_agent_service_phase17b2r.py` (2 tests). Regression: 130/130 agent-tagged tests pass.
2. `retention_service.py` (12 sites) — `test_tenant_context_retention_service_phase17b2r.py` (2 tests). Regression: 26/26 retention-tagged tests pass.
3. `business_blueprint_service.py` (12 sites) — `test_tenant_context_business_blueprint_service_phase17b2r.py` (2 tests). Regression: 48/48 blueprint-tagged tests pass (including the pre-existing "no vertical branch in business_blueprint_service source" structural test — confirmed my edit didn't add any vertical-specific logic).
4. `referral_service.py` (11 sites) — `test_tenant_context_referral_service_phase17b2r.py` (2 tests). Regression: 14/14 referral-tagged tests pass.
5. `payment_service.py` (11 sites, including the Stripe-linked `decide_refund`/`reconcile_external_refund` flows — special care per task §13: no auth/signature logic touched, only `set_tenant_context` added using the already-trusted `tenant_id` parameter) — `test_tenant_context_payment_service_phase17b2r.py` (2 tests). Regression: 144/144 payment/refund-tagged tests pass.
6. `business_journey_service.py` (11 sites, including the special `lock_session` — a Postgres advisory-lock session opened via `self._session_factory()` directly rather than the `async with` pattern used everywhere else, a genuinely distinct site type this round's audit specifically caught) — `test_tenant_context_business_journey_service_phase17b2r.py` (2 tests). Regression: 22/22 journey-tagged tests pass.
7. `company_memory_service.py` (10 sites) — `test_tenant_context_company_memory_service_phase17b2r.py` (2 tests). Regression: 142/142 memory-tagged tests pass.

New tests this round: **14**, all passing. Session-open sites fixed this
round: **80** (13+12+12+11+11+11+10). All 46 `phase17b2r`-tagged tests
across all 5 rounds re-run together at the end of this round: 46/46 pass.

**Running total across rounds 2-5: 19 files newly fixed by 17B-2R (168
session-open sites: 88 from rounds 2-4 + 80 this round), 19 files
re-confirmed already correct from Phase 17B-2, 2 files re-confirmed
correctly-excluded, 1 file trivially clean (0 sites). 46 tests total, all
passing, zero regressions anywhere across 5 rounds.**

## Round 6 summary (this round)

Fixed and tested all 8 services the coordinator listed, with careful
manual method-body reads (not just grep/batch-script) for every file per
the coordinator's explicit instruction — this caught 3 non-trivial special
cases this round alone:

1. `mcp_service.py` (7 sites — 2 classes, `McpExposureService` +
   `McpCredentialService`) — 6 sites fixed; `McpCredentialService.
   authenticate()` (the 7th) is the tenant-resolution step itself
   (looks up a credential by hashed token — no tenant_id exists until the
   lookup returns one), correctly classified GLOBAL/authentication-
   boundary, same treatment as the Stripe webhook dedup lookup, and left
   un-instrumented with an explanatory code comment added. Test:
   `test_tenant_context_mcp_service_phase17b2r.py` (4 tests, including one
   that specifically proves `authenticate()` never calls
   `set_tenant_context` regardless of whether the token is valid).
   Regression: 30/30 mcp-tagged tests pass.
2. `license_service.py` (7 sites, including `detect_expiring`'s nested
   per-license-row loop sessions) — `test_tenant_context_license_service_phase17b2r.py`
   (2 tests). Regression: 8/8 compliance/license-tagged tests pass.
3. `job_transition_service.py` (7 sites — `dispatch`/`start`/`complete`/
   `cancel` all delegate to `_apply_transition`, no separate sites) —
   `test_tenant_context_job_transition_service_phase17b2r.py` (2 tests).
   Regression: 67/67 job-tagged tests pass.
4. `contract_service.py` (7 sites, including `get_for_public_view` whose
   `tenant_id` comes from the documented public-token URL boundary per
   §21) — `test_tenant_context_contract_service_phase17b2r.py` (2 tests).
   Regression: 21/21 contract-tagged tests pass.
5. `approval_execution_service.py` (7 sites — the security-sensitive Agent
   approval CAS flow, given the same careful treatment as
   `payment_service.py`: no auth/state-transition logic touched anywhere,
   only `set_tenant_context` added using the already-trusted `tenant_id`
   parameter) — `test_tenant_context_approval_execution_service_phase17b2r.py`
   (3 tests, driven through the real `ToolRegistry`/approval flow, not
   mocked). Regression: 75/75 approval-tagged tests pass.
6. `voice_call_service.py` (7 sites) — `test_tenant_context_voice_call_service_phase17b2r.py`
   (2 tests). Regression: 17/17 voice_call/voice_conversation-tagged tests pass.
7. `vertical_extension_service.py` (7 sites, genuinely mixed — the kind of
   case the coordinator asked us to keep watching for: 4 sites on
   `VerticalExtension` itself, a GLOBAL platform catalog table with no
   `tenant_id` column at all, correctly NEVER call `set_tenant_context`;
   3 sites on `OrganizationVerticalExtension`, the real per-tenant opt-in
   row, DO call it) — `test_tenant_context_vertical_extension_service_phase17b2r.py`
   (3 tests, including one asserting the spy is NEVER called during the 4
   global-catalog methods). Regression: 51/51 vertical-tagged tests pass.
8. `team_service.py` (7 sites, also genuinely mixed — 5 standard
   tenant_id-param sites, plus `preview_invite`/`accept_invite`, whose
   tenant identity comes from this codebase's own signed invite JWT,
   verified inside `_resolve_pending_invite` BEFORE `set_tenant_context`
   is ever called, matching the "resolve trusted identity first, stamp
   after" shape used for `mcp_service.py`'s `authenticate` and the Stripe
   webhook dedup lookup) — `test_tenant_context_team_service_phase17b2r.py`
   (4 tests, including one proving a forged/invalid token is rejected
   with zero `set_tenant_context` calls). Regression: 14/14 team/invite-
   tagged tests pass.

New tests this round: **22**, all passing. Raw sites across these 8 files:
56 (7 each). Per-file breakdown of fixed vs. correctly-excluded:

| File | Raw sites | Fixed (set_tenant_context) | Correctly excluded |
|---|---|---|---|
| mcp_service.py | 7 | 6 | 1 (`authenticate` — auth boundary) |
| license_service.py | 7 | 7 | 0 |
| job_transition_service.py | 7 | 7 | 0 |
| contract_service.py | 7 | 7 | 0 |
| approval_execution_service.py | 7 | 7 | 0 |
| voice_call_service.py | 7 | 7 | 0 |
| vertical_extension_service.py | 7 | 3 | 4 (global catalog) |
| team_service.py | 7 | 7 | 0 |
| **Total** | **56** | **51** | **5** |

All 68 `phase17b2r`-tagged tests across all 6 rounds re-run together at
the end of this round: 68/68 pass.

**Running total across rounds 2-6: 27 files newly fixed by 17B-2R (219
session-open sites genuinely fixed: 168 from rounds 2-5 + 51 this round),
5 additional sites this round correctly classified excluded and
documented (see table above), 19 files re-confirmed already correct from
Phase 17B-2, 2 files re-confirmed correctly-excluded, 1 file trivially
clean (0 sites). 68 tests total, all passing, zero regressions anywhere
across 6 rounds.**

## Round 7 summary (this round)

Fixed and tested 8 more files — the coordinator's named 5-file batch
(`outbound_service.py`, `recommendation_service.py`, `quote_service.py`,
`insight_service.py`, `content_service.py`) plus 3 more from the 5-6-site
tier (`warranty_service.py`, `vendor_service.py`, `policy_service.py`),
all with the same full-method-body-read discipline:

1. `outbound_service.py` (6 sites, all genuinely tenant-scoped) —
   `test_tenant_context_outbound_service_phase17b2r.py` (2 tests).
   Regression: 4/4 outbound-tagged tests pass.
2. `recommendation_service.py` (9 sites — another genuinely mixed file: 7
   tenant-scoped sites fixed; 2 sites, `_load_providers` and the
   `VerticalExtension`-by-id lookup inside `_collect_capability_requirements`,
   only ever read GLOBAL catalog tables and are correctly excluded, each
   with an explanatory code comment) —
   `test_tenant_context_recommendation_service_phase17b2r.py` (2 tests).
   Regression: 49/49 recommendation-tagged tests pass, including the
   pre-existing "no vertical branch in recommendation_service source"
   structural test.
3. `quote_service.py` (8 sites, including `get_for_public_view`, the
   public quote-view-token boundary per §21) —
   `test_tenant_context_quote_service_phase17b2r.py` (2 tests).
   Regression: 79/79 quote-tagged tests pass (1 pre-existing skip).
4. `insight_service.py` (8 sites — all 8 read-only Morning Brief snapshot
   methods) — `test_tenant_context_insight_service_phase17b2r.py` (2
   tests, including a direct cross-tenant data-leak proof on
   `finance_snapshot`). Regression: 11/11 insight-tagged + 33/33
   morning_brief-tagged tests pass.
5. `content_service.py` (8 sites) —
   `test_tenant_context_content_service_phase17b2r.py` (2 tests).
   Regression: 60/60 content/marketing_content-tagged tests pass.
6. `warranty_service.py` (5 sites, same pattern as `license_service.py`) —
   `test_tenant_context_warranty_service_phase17b2r.py` (2 tests).
   Regression: 2/2 warranty-tagged tests pass.
7. `vendor_service.py` (5 sites) —
   `test_tenant_context_vendor_service_phase17b2r.py` (2 tests).
   Regression: 9/9 vendor-tagged tests pass.
8. `policy_service.py` (5 sites, tenant tool-policy overrides) —
   `test_tenant_context_policy_service_phase17b2r.py` (2 tests, including
   a cross-tenant override-isolation proof). Regression: 38/38
   automation_policy/compliance-tagged tests pass.

New tests this round: **16**, all passing. Session-open sites: 56 raw
across these 8 files, 54 fixed + 2 correctly excluded (both in
`recommendation_service.py`, the global-catalog reads). All 84
`phase17b2r`-tagged tests across all 7 rounds re-run together at the end
of this round: 84/84 pass.

**Running total across rounds 2-7: 35 files newly fixed by 17B-2R (273
session-open sites genuinely fixed: 219 through round 6 + 54 this round),
7 more sites now correctly classified excluded and documented (5 in round
6 + 2 this round, plus the 1 mcp_service.py authenticate exclusion and 4
vertical_extension_service.py exclusions already counted in round 6's own
total — see each round's own summary for exact per-file breakdowns), 19
files re-confirmed already correct from Phase 17B-2, 2 files
re-confirmed correctly-excluded, 1 file trivially clean. 84 tests total,
all passing, zero regressions anywhere across 7 rounds.**

## Round 8 summary (this round)

Fixed and tested the coordinator's named 5-file batch, all in the 5-site
tier, same full-method-body-read discipline:

1. `nurture_service.py` (5 sites, same shape as `outbound_service.py`) —
   `test_tenant_context_nurture_service_phase17b2r.py` (2 tests).
   Regression: 7/7 nurture-tagged tests pass.
2. `knowledge_service.py` (5 sites, Company-OS Knowledge Layer) —
   `test_tenant_context_knowledge_service_phase17b2r.py` (2 tests).
   Regression: 89/89 knowledge-tagged tests pass.
3. `integration_connection_service.py` (5 sites) —
   `test_tenant_context_integration_connection_service_phase17b2r.py` (2
   tests). Regression: 24/24 integration_connection-tagged tests pass.
4. `campaign_service.py` (5 sites) —
   `test_tenant_context_campaign_service_phase17b2r.py` (2 tests).
   Regression: 8/8 campaign-tagged tests pass.
5. `billing_service.py` (5 sites — another genuinely mixed file, and
   Stripe-linked, given the same careful treatment as
   `payment_service.py`/`approval_execution_service.py`: 3 sites take
   `tenant_id`/`org.id` directly; `apply_subscription_updated`/
   `apply_subscription_deleted` (2 sites) take a Stripe `subscription_id`,
   not a tenant_id — tenant identity is resolved by looking up the
   Organization whose `stripe_subscription_id` matches (the id from an
   already-signature-verified Stripe webhook payload — see
   `app/api/v1/billing.py`'s own signature-verification gate upstream of
   these calls), then `set_tenant_context` is called using that resolved
   `org.id`, strictly AFTER resolution — the same "resolve trusted
   identity first, stamp after" shape as `McpCredentialService.
   authenticate`/`TeamService.preview_invite`/round 6's own findings) —
   `test_tenant_context_billing_service_phase17b2r.py` (4 tests,
   including one proving `set_tenant_context` is called exactly once,
   with the resolved org's own id, and one proving an unknown
   subscription_id is a safe no-op that never fabricates a tenant).
   Regression: 18/18 billing-tagged + 4/4 phase22-stripe-hardening tests
   pass.

New tests this round: **12**, all passing. Session-open sites fixed this
round: **25** (5 files x 5 sites each), all genuinely tenant-scoped
(billing_service.py's 2 resolve-then-stamp sites counted as fixed, not
excluded — they DO set real tenant context, just after a resolution
step, unlike the true exclusions like `authenticate()` or the global
catalog reads). All 96 `phase17b2r`-tagged tests across all 8 rounds
re-run together at the end of this round: 96/96 pass.

**Running total across rounds 2-8: 40 files newly fixed by 17B-2R (298
session-open sites genuinely fixed: 273 through round 7 + 25 this
round), 19 files re-confirmed already correct from Phase 17B-2, 2 files
re-confirmed correctly-excluded, 1 file trivially clean. 96 tests total,
all passing, zero regressions anywhere across 8 rounds.**

## Round 9 summary (this round)

Fixed and tested the coordinator's full 4-site-tier batch (7 files), same
full-method-body-read discipline:

1. `seo_service.py` (4 sites) —
   `test_tenant_context_seo_service_phase17b2r.py` (2 tests). Regression:
   22/22 seo-tagged tests pass.
2. `retention_campaign_service.py` (4 sites) —
   `test_tenant_context_retention_campaign_service_phase17b2r.py` (2
   tests). Regression: 8/8 retention_campaign-tagged tests pass.
3. `local_service.py` (4 sites) —
   `test_tenant_context_local_service_phase17b2r.py` (2 tests, the only
   test coverage this service has ever had — no pre-existing test file
   existed for it).
4. `integration_catalog_service.py` (4 sites — genuinely 0 tenant-scoped:
   every session-open site operates only on the GLOBAL
   `IntegrationProviderCatalog` table, same classification as
   `_load_providers`/the `VerticalExtension`-by-id read found in earlier
   rounds. Added a Phase 17B-2R classification comment to the class and
   proved it two ways in
   `test_tenant_context_integration_catalog_service_phase17b2r.py`: (a)
   the module never even imports `set_tenant_context`, and (b) a catalog
   entry created under one tenant's ambient DB context is fully visible
   when read back under a different tenant's context — functionally
   proving it was never filtered by tenant at all). Regression: 17/17
   integration_catalog/provider_catalog-tagged tests pass.
5. `google_calendar_sync_service.py` (4 sites, including
   `sync_appointment`'s own session, which additionally takes a real
   PostgreSQL advisory lock — a third instance this phase of the
   "session does something beyond a plain query" pattern first flagged
   in `business_journey_service.py` round 5) —
   `test_tenant_context_google_calendar_sync_service_phase17b2r.py` (2
   tests). Regression: 53/53 google_calendar-tagged tests pass, including
   the dedicated real-Postgres concurrency test for this exact advisory
   lock.
6. `ar_service.py` (4 sites) —
   `test_tenant_context_ar_service_phase17b2r.py` (2 tests, including a
   direct cross-tenant aging-summary data-leak proof). Regression: 5/5
   ar-tagged + finance_e2e AR assertions pass.
7. `adjustments_service.py` (4 sites — credit-note and write-off
   request/decide, both approval-gated) —
   `test_tenant_context_adjustments_service_phase17b2r.py` (2 tests).
   Regression: 6/6 adjustments/credit_note/writeoff-tagged tests pass.

New tests this round: **15**, all passing. Session-open sites: 28 raw
across these 7 files (4 each), 24 fixed + 4 correctly excluded (all in
`integration_catalog_service.py`, the fully-global file). All 111
`phase17b2r`-tagged tests across all 9 rounds re-run together at the end
of this round: 111/111 pass.

**Running total across rounds 2-9: 46 files newly fixed by 17B-2R (322
session-open sites genuinely fixed: 298 through round 8 + 24 this round),
1 additional file (`integration_catalog_service.py`) fully classified as
requiring no fix at all, 19 files re-confirmed already correct from Phase
17B-2, 2 files re-confirmed correctly-excluded, 1 file trivially clean.
111 tests total, all passing, zero regressions anywhere across 9 rounds.**

## Round 10 summary (this round)

Fixed and tested the coordinator's named 7-file batch
(`review_service.py`, `reactivation_service.py`,
`quickbooks_import_service.py`, `qa_service.py`,
`knowledge_retrieval_service.py`, `job_service.py`,
`job_costing_service.py`), then continued through 7 more files in the
1-2-site tier to deliver a larger batch as requested — **14 files total
this round**, same full-method-body-read discipline throughout:

1. `review_service.py` (3 sites). Regression: 20/20 review-tagged tests pass.
2. `reactivation_service.py` (3 sites). Regression: 3/3.
3. `quickbooks_import_service.py` (3 sites — test includes a same-external-id
   cross-tenant-matching proof: two tenants importing a QuickBooks customer
   sharing the same external id each get their own separate Customer row,
   never cross-matched). Regression: 136/136 quickbooks-tagged tests pass.
4. `qa_service.py` (3 sites). Regression: 51/51 qa-tagged tests pass.
5. `knowledge_retrieval_service.py` (3 sites, real pgvector search).
   Regression: 91/91 knowledge-tagged tests pass.
6. `job_service.py` (3 sites — including a session opened as `fresh_session`
   inside `create_job`'s IntegrityError-recovery path, a distinct variable
   name the mechanical batch-insertion script used everywhere else this
   phase would have silently skipped; caught by reading the method body,
   fixed by hand, and specifically tested with a real concurrent-duplicate-
   key scenario proving BOTH sessions in that method set context).
   Regression: 72/72 job-tagged tests pass.
7. `job_costing_service.py` (3 sites). Regression: 2/2 (only test coverage
   this service has ever had).
8. `quote_deposit_service.py` (2 sites, Stripe-linked — same careful
   treatment as `payment_service.py`/`billing_service.py`: no auth/
   signature logic touched, only `set_tenant_context` added). Regression:
   40/40 quote_deposit-tagged tests pass.
9. `task_service.py` (2 sites). Regression: 12/12.
10. `material_service.py` (2 sites). Regression: 9/9.
11. `exception_service.py` (2 sites — a widely-depended-on service:
    `WarrantyService`, `LicenseService`, `CampaignService`,
    `RetentionService`, and others already fixed this phase all call into
    it, so this fix closes the tenant-context chain for all of them too).
    Regression: 20/20 exception-tagged, plus 30/30 across
    warranty/license/campaign/vendor/ar_service re-run to confirm no
    transitive breakage.
12. `completion_service.py` (2 sites). Regression: 14/14.
13. `scope_change_service.py` (1 site). Regression: 2/2.
14. `signoff_service.py` (1 site). Regression: 2/2.

New tests this round: **31**, all passing. Session-open sites fixed this
round: **31** across 14 files, all genuinely tenant-scoped (zero
exclusions found this round — every site in every one of these 14 files
turned out to be a straightforward tenant-owned query). All 142
`phase17b2r`-tagged tests across all 10 rounds re-run together at the end
of this round: 142/142 pass.

**Running total across rounds 2-10: 61 files newly fixed by 17B-2R (353
session-open sites genuinely fixed: 322 through round 9 + 31 this round),
1 additional file fully classified as requiring no fix
(`integration_catalog_service.py`), 19 files re-confirmed already correct
from Phase 17B-2, 2 files re-confirmed correctly-excluded, 1 file
trivially clean. 142 tests total, all passing, zero regressions anywhere
across 10 rounds.**

## Round 11 summary (this round)

Fixed and tested the coordinator's full named batch for this round — **15
files, 18 session-open sites** — plus classified one more file GLOBAL and
confirmed one more file already fully instrumented:

1. `ai_invocation_log_service.py` (1 site — module-level
   `record_ai_invocation(session_factory, *, tenant_id, ...)` function, not
   a class method; fix applied the same way). Test: 1 file, both a positive
   and cross-tenant check folded into the combined AI-services test file.
2. `ai_next_action_service.py` (2 sites: `decide_quote_followup`,
   `decide_invoice_followup`).
3. `ai_qualification_service.py` (1 site: `generate_recommendation` —
   confirmed the session block runs unconditionally before the
   `is_connected` early-return, so the fix is exercised even against the
   deterministic test AI provider).
4. `attachment_service.py` (1 site: `add_document`).
5. `cash_forecast_service.py` (2 sites: `generate`, `weekly_projection`).
   **Flagged, not fixed** (out of this phase's plumbing-only scope): found
   `weekly_projection` does `session.get(CashForecast, forecast_id)` with
   NO tenant_id ownership check afterward, unlike every other tenant-scoped
   lookup in the codebase — a real pre-existing app-level cross-tenant
   metadata leak. Flagged via `spawn_task` (task_id `task_7574b1e7`) for
   separate follow-up rather than fixed here.
6. `collection_service.py` (2 sites: `schedule_next_action`,
   `execute_due_actions`).
7. `conversion_service.py` (1 site: `convert_and_book`).
8. `delay_detection_service.py` (1 site: `run`).
9. `operations_communication_service.py` (1 site: `_customer_email`).
10. `openai_realtime_voice_service.py` (1 site: `_identify_caller` — the
    OpenAI Realtime bridge's own caller-identification lookup, reuses the
    exact same `find_matching_customer` call as #13 below).
11. `owner_activity_service.py` (1 site: `_collect_all`).
12. `owner_attention_service.py` (1 site: `get_attention_queue` — verified
    its `WebhookEvent.tenant_id == tenant_id` filter is a legitimate
    tenant-scoped read, consistent with the fix).
13. `voice_conversation_service.py` (1 site: `_identify_caller` — identical
    shape to #10; both call `find_matching_customer(session, tenant_id=...,
    phone=caller_number)`, so both got matching tests proving a caller
    phone number registered under tenant A is never resolved to a Customer
    when tenant B's id is passed).
14. `quickbooks_sync_service.py` (1 site: `sync_invoice` — advisory-locked
    session; `set_tenant_context` placed before the
    `pg_advisory_xact_lock` call).
15. `quickbooks_payment_sync_service.py` (2 sites: `sync_deposit_payment`,
    `sync_invoice_payment_to_quickbooks` — both advisory-locked).
16. `quickbooks_refund_sync_service.py` (1 site: `sync_refund_to_quickbooks`
    — advisory-locked).

Also this round:
- `website_generation_service.py`'s `_resolve_vertical_data_sources`
  inspected and classified **GLOBAL** (no fix) — it only reads the
  platform-wide `VerticalExtension` catalog table, never the tenant-scoped
  `OrganizationVerticalExtension` join (already queried earlier via a
  different, already-tenant-scoped session). A code comment was added
  documenting the classification, matching the existing pattern in
  `recommendation_service.py::_collect_capability_requirements`.
- `app/workflows/activities.py` inspected and found **already fully
  instrumented** — both session-open sites in
  `resume_automation_execution_activity` already called
  `set_tenant_context`. No code change made.

New tests this round: **10 new test files** (one file combines the
`ai_invocation_log_service.py` + `ai_qualification_service.py` proofs, per
the coordinator's original round-11 instruction ordering) covering all 15
fixed files, **31 individual test functions**, all passing against real
PostgreSQL. Every test follows the same `_ContextSpy` methodology used in
every prior round (positive test proving `set_tenant_context` is called
with the correct tenant_id and a same-session `current_setting` readback
matches, plus a dedicated cross-tenant-denial test).

Two non-trivial fixture issues surfaced and fixed while writing these
tests (both about realistic setup, not about the production fix): (a)
`Customer.phone_normalized` is a plain, non-computed column — the voice
caller-ID tests had to call the real `normalize_phone()` helper explicitly
when seeding test Customers, matching what `find_matching_customer`
actually queries against; (b) `VoiceConversationService`/
`OpenAIRealtimeVoiceBridge` both need a real, event-bus-wired
`ToolRegistry` (the `tool_registry` pytest fixture from `conftest.py`),
not a bare `ToolRegistry()`.

Regression: ran every existing test file that touches any of the 15+2
files above (34 test files spanning AI next-action/qualification, finance
domain/e2e, idempotency/sweep concurrency, exceptions/communication, the
full OpenAI Realtime + voice conversation + website-generation suites, and
all 7 QuickBooks-related test files including all 3 real-Postgres
advisory-lock concurrency proofs) — **265 passed, 1 failed**. The 1
failure (`test_voice_stream_route_dispatches_to_realtime_engine_when_
configured`) was confirmed to be a pre-existing test-isolation artifact,
not a real regression: it passes cleanly (1/1) when run in isolation, and
only fails when sharing an event loop/connection pool with dozens of other
DB-heavy async test files in one process — the same class of harmless
cross-file interference noted as the "1 pre-existing failure" baseline
from Phase 17B-2's full-suite runs. `py_compile` clean across all 18
touched files. All 173 `phase17b2r`-tagged tests across all 11 rounds
re-run together at the end of this round: **173/173 pass**.

**Running total across rounds 2-11: 76 files newly fixed by 17B-2R (18
session-open sites fixed this round on top of 353 through round 10 = 371
total), 2 files classified GLOBAL/no-fix-needed, 19 files re-confirmed
already correct from Phase 17B-2, 1 file confirmed already fully
instrumented this round, 1 out-of-scope app-level bug flagged (not fixed)
via `spawn_task`. 173 tests total, all passing, zero genuine regressions
anywhere across 11 rounds.**

## Next steps (for the next resumed round)

1. Continue the file-level fixes for the remaining ~33 still-PENDING
   files. Per the coordinator's round-11 instruction, the next priority is
   the `app/tools/builtin/*.py` files beyond `crm_tools.py` (round 4) —
   most of their own Tools likely delegate entirely to now-fixed services,
   so a quick pass per file should mostly just confirm no separate fix is
   needed, fixing only the minority of Tools with their OWN direct session
   sites.
2. Also still pending: the authenticated HTTP router files
   (`appointments.py`, `automations.py`, `crm.py`, `dashboard.py`,
   `events.py`, `marketing.py`, `operations.py`, `retention.py`, etc.) —
   `jobs.py` (round 4) is the only one audited so far; most of these
   likely delegate to already-fixed services via Tools, same caveat as
   above. This is the coordinator's explicitly named next phase of work
   ("move to the remaining tool files and HTTP routers, verifying your
   hypothesis that most delegate to now-fixed services").
3. See the full sorted "File-level first pass" table above for the exact
   remaining ~33-file order beyond the tools/routers work.
4. Keep reading every method body manually rather than trusting grep/
   batch-script counts alone — this discipline has now caught 9+
   non-trivial special cases across rounds 4-11 (auth boundaries,
   global-vs-tenant-scoped splits, token/subscription-id-verified-
   tenant-identity cases, advisory-locked sessions, oddly-named session
   variables, an already-fully-instrumented file, and a genuine
   out-of-scope app-level authorization bug) that a naive count-based
   approach would have gotten wrong or silently missed.
5. Only after the remaining named/priority work is done does the full
   1896-test regression run happen (task §25) — NOT done yet, intentionally
   deferred.
6. The real Postgres instance bootstrapped in round 2 is still running in
   this session's background (scratchpad `pgdata` dir) — the next resumed
   round should check `ps aux | grep start_pg.py` before re-bootstrapping.

## Round 12 summary (this round — picked up after a host restart lost the
## prior round-12 session; all its file changes had persisted on disk)

Verified the interrupted round's own work, discovered it had actually
completed far more than its own last-known checkpoint suggested (all
`app/tools/builtin/*.py` files and all named authenticated HTTP routers
were already fixed + had test files on disk, just not yet re-run or
recorded in this document), then closed out the last remaining genuine
gaps found by a full re-sweep of the entire `app/` tree.

**1. Verified `marketplace_webhooks.py`** (the file the interrupted round
was mid-way through): confirmed the 1 session-open site's fix is correct
(signature verified at line 94, strictly BEFORE `set_tenant_context` is
called at line 115 inside the session opened at line 111 — never trusts
the path-param `tenant_id` before that). Ran the existing
`test_tenant_context_marketplace_webhooks_phase17b2r.py` against real
Postgres — passed. Added a second test,
`test_marketplace_lead_webhook_never_crosses_tenant_context`, a proper
cross-tenant-denial proof (two tenants' webhook deliveries in the same
test, asserts each call's GUC readback matches THAT call's own tenant,
never the other) — the original test file only had the positive-path
proof. Regression: 11/11 (2 new + 9 pre-existing
`test_marketplace_lead_webhooks.py`) pass.

**2. Verified all 13 `app/tools/builtin/*.py` files beyond `crm_tools.py`**
(`event_tools.py` 5, `job_tools.py` 4, `worker_tools.py` 3,
`morning_brief_tools.py` 3, `approval_tools.py` 3,
`organization_tools.py` 2, `invoice_tools.py` 2, `audit_tools.py` 2,
`stripe_tools.py` 1, `quote_tools.py` 1, `notification_tools.py` 1,
`job_summary_tool.py` 1, `automation_policy_tools.py` 1 — 29 sites total):
all already had `set_tenant_context` call count == site count on disk,
each using `context.tenant_id` (the `ToolRegistry`-validated
`ExecutionContext`, never a raw client value — spot-checked
`event_tools.py`'s diff directly to confirm the pattern). Ran all 13
matching `test_tenant_context_*_tools_phase17b2r.py` /
`test_tenant_context_job_summary_tool_phase17b2r.py` files against real
Postgres: **25/25 pass**.

**3. Verified all 11 remaining named authenticated HTTP routers**
(`appointments.py` 1, `automations.py` 2, `crm.py` 1, `dashboard.py` 1,
`events.py` 1, `marketing.py` 1, `operations.py` 1, `retention.py` 4,
`mcp_admin.py` 1, `morning_brief.py` 2, `public_quotes.py` 1): all already
had `set_tenant_context` call count == site count on disk. Also
re-inspected `billing.py` (2 sites, 1 `set_tenant_context` call — the
apparent mismatch): confirmed correctly excluded, not a gap — the first
session (line 148) only creates/updates the `WebhookEvent` row itself
before tenant identity is knowable (genuinely no tenant to stamp yet,
same "resolve-then-stamp" shape as `app/api/v1/webhooks.py`'s own Stripe
dedup lookup), and the second session (line 212) resolves `tenant_id` from
the already-signature-verified payload metadata and calls
`set_tenant_context(session, tenant_id)` — correctly handling the `None`
case (subscription-lifecycle events carry no tenant metadata) as a
documented safe no-op, never a fabricated value. Ran all 11 matching
`test_tenant_context_*_router_phase17b2r.py` files against real Postgres:
**12/12 pass**.

**4. Full-tree re-sweep for ANY remaining ZERO_CTX or partial-mismatch
file** (not limited to the coordinator's named list — grepped every file
under `app/` matching `session_factory()`/`async_session_maker()`/
`AsyncSession(` and compared against its own `set_tenant_context(` call
count, this time WITHOUT the naive `grep -v test` filename filter every
prior round used, which silently excluded any file with the substring
"test" in its name — including three genuinely production files:
`app/communications/internal_test_adapter.py`,
`app/invoice_delivery/internal_test_adapter.py`, and
`app/calendar/internal_test_adapter.py`). This found **5 real, previously
undetected gaps**, all fixed this round:

- `app/communications/internal_test_adapter.py` (1 site: `_log`, called
  from both `send_email`/`send_sms`) — fixed.
- `app/communications/twilio_adapter.py` (1 site: `_log`, the real Twilio
  SMS provider's own audit-log write) — fixed. No auth/signature logic
  touched (Twilio's own outbound HTTP call is unrelated to tenant
  context); only `set_tenant_context` added using the already-trusted
  `tenant_id` parameter.
- `app/communications/sendgrid_adapter.py` (1 site: `_log`, same shape as
  the Twilio adapter) — fixed.
- `app/invoice_delivery/internal_test_adapter.py` (2 sites: `send_invoice`,
  `send_quote`) — fixed.
- `app/calendar/internal_test_adapter.py` (3 sites: `get_availability`,
  `create_event` — including its own PostgreSQL advisory-lock session, a
  4th instance this phase of that pattern — and `update_event`;
  `cancel_event` delegates to `update_event`, no separate site) — fixed.

Also classified `app/main.py`'s `/ready` readiness-probe endpoint (2
sites: `_check_database`, `_check_migration_head`) as **GLOBAL** — both
sessions run only `SELECT 1` / read `alembic_version`, no tenant-owned row
touched at all, and a code comment was added documenting this. Not a
new finding of a mismatch (0 sites needed fixing), but the file was
previously untouched/uninspected by name in this document.

Confirmed via the same full-tree sweep that **every other mismatch found
was one of the 9 already-documented, already-tested correctly-excluded
classifications from prior rounds** (`billing.py`, `webhooks.py`,
`bus.py`, `vertical_extension_service.py`, `mcp_service.py`,
`integration_catalog_service.py`, `website_generation_service.py`,
`recommendation_service.py`, `morning_brief_service.py`) — no new gaps
among them.

New test file this round:
`tests/test_tenant_context_delivery_adapters_phase17b2r.py` (6 tests,
covering all 5 newly-fixed adapter files: 4 positive `_ContextSpy` proofs
including the Twilio/SendGrid real-provider adapters with their outbound
`httpx` calls mocked out, plus 1 dedicated cross-tenant-denial proof for
the calendar adapter — tenant B's `update_event` call against tenant A's
appointment id correctly raises `ValueError("Appointment not found")`,
never reaching or mutating the row). All 6 pass against real Postgres.
Regression: `test_exceptions_and_communication.py`,
`test_google_calendar_integration.py`, `test_google_calendar_pull.py`
(54/54 pass); `test_readiness.py` + `test_observability.py` (19/19 pass,
confirming the `main.py` comment-only change didn't alter `/ready`
behavior).

**File-level audit is now genuinely complete.** A full-tree grep
comparing `set_tenant_context(` call count against session-open site
count for every file under `app/` shows **zero unexplained mismatches**
— every one of the 131 originally-inventoried files, plus the 5
newly-discovered adapter files (bringing the true total to 136), plus
`app/main.py`, now either has full 1:1 coverage or a specific, tested,
documented CROSS_TENANT_SYSTEM/GLOBAL classification.

New tests this round: **1 new test file for marketplace_webhooks.py's
missing cross-tenant test (1 test) + 1 new combined delivery-adapters
test file (6 tests) = 7 new tests**, all passing. All pre-existing
`phase17b2r`-tagged tests re-run together at the end of this round:
**218/218 pass** (211 pre-existing on disk from rounds 1-11 plus the
interrupted round 12's own tool/router work + 7 new this round — note the
211 figure includes the interrupted round's on-disk tool/router tests
that were never counted in round 11's own tally of 173, since that work
happened after round 11's write-up).

**Running total across all rounds: essentially all ~136 tenant-scoped
files now fixed+tested or correctly classified GLOBAL/CROSS_TENANT_SYSTEM
and documented. 218 `phase17b2r`-tagged tests total, all passing, zero
regressions found anywhere in this round's extensive re-verification.**

## Next steps (for the next round, if any)

1. Run the full unfiltered backend regression suite (`pytest -q`, all
   ~1900 tests) and compare against the Phase 17B-2 baseline
   (1896 passed / 1 pre-existing voice-websocket failure / 12 skipped).
   This was kicked off in the background at the end of round 12 — check
   its result before re-running.
2. If the full suite is clean (or shows only the same pre-existing
   failure), write the final
   `PHASE_17B2R_COMPLETE_TENANT_CONTEXT_PROPAGATION_LOG.md` deliverable
   per the task's required section list. Do not write it before the full
   suite has actually been run and reviewed.
3. The out-of-scope `CashForecastService.weekly_projection` cross-tenant
   metadata-leak bug flagged in round 11 (`spawn_task` id
   `task_7574b1e7`) remains intentionally unfixed — leave it for its own
   separate task, not 17B-3.
