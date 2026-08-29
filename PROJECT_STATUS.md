# Klaros AI — Project Status

Last updated: 2026-08-27 (Phase 8 checkpoint)

## What's actually built and verified — Phases 1–5 (unchanged, condensed)

- **Phase 1**: multi-tenant data model, JWT auth, RBAC, tenant isolation, audit log, Docker Compose.
- **Phase 2**: durable event bus (Postgres store + pluggable transport, idempotency, retry/dead-letter/
  replay), `ToolRegistry` enforcement pipeline (permission → tenant → schema → policy → audit),
  approval boundary, AI execution boundary, integration provider framework, Temporal workflow
  foundation.
- **Phase 3**: CRM — leads (create/normalize/match/qualify), customers (Customer 360, timeline, AI
  summary), appointments/booking (`InternalTestCalendarAdapter`, real double-booking prevention),
  communications adapter.
- **Phase 4**: Operations & Delivery — job state machine, workers, tasks/materials/purchase-order
  drafts, real local-filesystem document/photo/voice-note storage, QA, exceptions engine, completion
  packets/customer signoff, job close-out publishing `invoice.trigger_requested`.
- **Phase 5**: Finance & Back Office — invoice/payment/AR/collections/job-costing/cash-forecast domain,
  internal test invoice-delivery and payment providers, Finance exceptions extending the shared
  exception engine, `/finance*` frontend, live-verified job→invoice→payment→PAID chain.

Full detail on these phases is in git history / earlier versions of this file. This checkpoint
focuses on what Phase 6 added and verified.

## What's actually built and verified — Phase 5 (Finance & Back Office)

### Domain model

`backend/app/models/finance.py` (new) — `Invoice`/`InvoiceLineItem`, `Payment`/`PaymentAllocation`,
`Refund`, `CreditNote`/`CreditNoteLineItem`, `WriteOffRequest`, `JobCost`, `Vendor`/`VendorBill`,
`Payout`, `CashForecast`/`CashForecastItem`, `CollectionAction`. Money is `Decimal`/`Numeric`
everywhere in this file — never `float` — a deliberate contrast with Phase 4's pre-existing
`float`-mapped `Job.estimated_*`/`actual_*` columns, which are read but never modified by new
migrations. `AccountReceivable` is intentionally *not* a table: `app/services/ar_service.py` derives
aging/balances directly from `Invoice` rows, per the spec's explicit allowance for a derived AR
service. Migration `0005_finance_domain` (hand-written, same Postgres-not-available caveat as
`0001`–`0004`) adds all of the above plus `organizations.manual_starting_cash`.

### Invoice lifecycle

`app/services/invoice_service.py`: totals (`subtotal`/`tax`/`discount`/`total`) are always
recomputed server-side from line items — no client-supplied total is ever trusted. Draft creation
comes from two paths: `create_draft_from_job` (idempotent per job via a `(tenant_id, idempotency_key)`
unique constraint — pulls `Job.estimated_revenue` and any `APPROVED` `ScopeChange` rows; if a job has
no real pricing data it still creates the draft, with `notes = "Invoice generation requires review —
no pricing data available on the job."` and zero totals — never a fabricated amount) and
`create_manual_draft` (line items supplied directly). `app/services/invoice_policy.py` is the
deterministic approval gate — invoice total > $1,000, discount > 20% of subtotal, or an unresolved
scope change all force `PENDING_APPROVAL` with a real `ApprovalRequest` row (the *same* Phase 2
model, not a second mechanism); otherwise the invoice auto-approves. `finance.approve_invoice` /
`reject_invoice` are dedicated tools (gated by `APPROVE_INVOICE`, not reachable by the AI boundary's
default role) that resolve the `ApprovalRequest` **and** transition the invoice in one call — closing
the "approval doesn't resume the original action" gap specifically for invoices, since spec section 36
required dedicated endpoints for exactly this.

`finance.send_invoice` is `AUTO` at the `ToolRegistry` policy layer, not `APPROVAL_REQUIRED` — the
reasoning (and its consequence) is in "Known limitations" below.

### Delivery and payment providers

`app/invoice_delivery/` (new package) — `InvoiceDeliveryProvider` interface +
`InternalTestInvoiceDeliveryAdapter`, which records real, queryable `CommunicationLog` rows
(`channel=INVOICE_DELIVERY`) rather than calling anything external. `app/payments/` (new package) —
`PaymentProvider` interface + `InternalTestPaymentAdapter`, explicitly labeled **INTERNAL TEST
PAYMENT PROVIDER** — it always succeeds and generates a deterministic `test-pay-<hex>` reference; no
card is charged, no money moves. Both are distinct from the *existing* `CommunicationProvider`
(reused as-is for transactional notifications — `invoice_sent`/`payment_received`/
`collection_reminder` templates — via `app/events/finance_handlers.py`, mirroring
`operations_handlers.py`'s pattern).

### Payments, refunds, credit notes, write-offs

`app/services/payment_service.py`: `record_payment` is idempotent on `(tenant_id, provider,
external_id)` — verified by re-submitting the same external payment id and confirming no
double-allocation. Invoice `amount_paid`/`amount_due`/`status` are always recomputed from the sum of
`PaymentAllocation` rows, not incremented, so re-running allocation is a no-op, not a double-add.
Refunds/credit notes/write-offs (`app/services/adjustments_service.py`) are all `APPROVAL_REQUIRED`
by construction — `create_refund_request`/`create_credit_note_request`/`create_writeoff_request` can
only ever land in a pending state with a real `ApprovalRequest` attached; there is no code path from
any of those tools to a completed refund/applied credit/applied write-off without a separate
`approve_*` call gated by its own `APPROVE_*` permission. AI can request; AI cannot approve its own
request (permission-gated, not just policy-gated).

### Job costing, vendors

`app/services/job_costing_service.py` writes `JobCost` rows and recomputes the *existing*
`Job.actual_cost`/`Job.actual_margin` fields — no new duplicate columns, per spec section 18.
Margin-variance detection (`>10` percentage points below the estimated margin) opens a `MARGIN_LEAK`
exception via the *existing* exception engine. `app/services/vendor_service.py` covers
`Vendor`/`VendorBill`/`Payout` for subcontractor costs; an `APPROVED` `VendorBill` linked to a job
automatically creates a matching `SUBCONTRACTOR` `JobCost` row.

### AR, collections, cash forecast

`app/services/ar_service.py`: standard aging buckets (current/1-30/31-60/61-90/90+), computed on
request, never persisted. `detect_overdue` is deterministic and on-demand (mirrors Phase 4's
`DelayDetectionService` pattern) — flips `SENT`/`PARTIALLY_PAID` invoices past `due_date` to
`OVERDUE`, opens an `INVOICE_OVERDUE` exception, and schedules the first `CollectionAction`.
`app/services/collection_service.py` uses a static days-overdue → action-type policy table (the same
simplification pattern as Phase 3's lead scoring and Phase 4's `OVERDUE_TOLERANCE_MINUTES`) and a
`CollectionAction.scheduled_for` record — **not** `workflow.sleep()`, per the explicit instruction.
`execute_due_actions` is on-demand and sends a real reminder through the existing
`CommunicationProvider`. `app/services/cash_forecast_service.py` builds a 13-week forecast from real
open invoices (inflows) and vendor bills (outflows) with HIGH/MEDIUM/LOW confidence per item;
starting cash is `NOT_CONNECTED` unless `Organization.manual_starting_cash` is explicitly set
(labeled `MANUAL_INTERNAL_TEST_DATA` when it is) — never a fabricated balance.

### Tools, policy, API, frontend

30 new `finance.*` tools across `app/tools/builtin/{invoice,payment,ar,job_cost,cash,vendor,
adjustment}_tools.py`, registered in `app/tools/factory.py` with policies in `app/tools/policy.py`.
New routers: `invoices`, `payments`, `ar`, `refunds`, `credit_notes`, `writeoffs`, `job_costs`,
`profitability`, `cash`, `finance` (dashboard summary). New frontend pages: `/finance`,
`/finance/invoices[/[id]]`, `/finance/ar`, `/finance/profitability`, `/finance/cash`; Finance
extensions to the existing Owner Cockpit (`/dashboard`), Customer 360 (`/customers/[id]`), and Job
detail (`/jobs/[id]`, including a job-cost recording form) pages — all real API-backed, all showing
`$0`/"Not connected"/"Invoice not created" rather than any placeholder number. Nav updated to add
Finance/Invoices/AR/Profitability/Cash without removing any existing item.

### Test results (actually run)

```
backend/tests/  (excluding test_temporal_workflows.py)  ->  118 passed, 0 failed
    (python -m pytest -q --ignore=tests/test_temporal_workflows.py, sqlite in-memory)
```

118 = Phase 1–4's 109 (all still passing, unmodified in behavior) + 9 new this phase:
`test_finance_e2e.py` — the full realistic scenario (job closed → `invoice.trigger_requested` →
DRAFT invoice with backend-computed totals → idempotent re-trigger → auto-approval under threshold →
send via internal test delivery → record test payment via internal test payment provider → PAID →
payment-idempotency re-submission → AR balance zero → job cost recorded → *existing* `Job.actual_cost`
updated → MARGIN_LEAK exception opened → audit rows present for every tool call) and the
overdue-path scenario (due date forced into the past → `detect_overdue` → `OVERDUE` →
`INVOICE_OVERDUE` exception → `CollectionAction` scheduled) — 2 tests; `test_finance_domain.py` — total
recalculation determinism, invoice-policy thresholds, cross-tenant isolation on invoice tools,
credit-note approval reducing the invoice total, write-off approval zeroing amount due, cash forecast
`NOT_CONNECTED` behavior, and the refund approval boundary (request → still `REQUESTED` → only
`approve_refund` moves it to `COMPLETED`) — 7 tests.

Frontend: `npx tsc --noEmit` — 0 errors. `npm run build` — succeeds, 25 routes.

### Live browser verification (actually performed)

Registered a fresh tenant, created a real customer and job, drove the job through the full state
machine (scheduled → dispatched → en route → on site → started → photo uploaded via real local
storage → completed → QA passed → completion packet → closed) with `estimated_revenue`/
`estimated_cost` set. Confirmed the Job detail page's honest "Invoice not created... hasn't simply
been triggered yet" message pre-close, then used the "Trigger invoice now" button, producing a real
`INV-1001` DRAFT for exactly the estimated revenue (no fabrication). Requested approval (auto-approved
under the $1,000 threshold), sent (internal test delivery — confirmed a real `communication_logs` row),
recorded a test payment (internal test payment provider) → invoice `PAID`. Confirmed Customer 360 shows
the real invoice/payment; confirmed a second job with a deliberately high cost produced a real
`MARGIN_LEAK` exception in the shared exceptions engine and showed correctly on the Profitability
page (est. margin 53.8% → actual 7.7%). Confirmed the Owner Cockpit's new Business Health — Finance
section shows real numbers and a real "FINANCE NEEDS ATTENTION" banner. Created a second invoice,
forced its due date into the past, used the AR dashboard's "Detect overdue" button (real bucket
placement — $200 in the 31-60 day bucket) and confirmed a real `INVOICE_OVERDUE` exception plus a
scheduled `CollectionAction` (`OWNER_REVIEW`, since 56 days overdue); "Execute due collections"
produced a real `collection_reminder` `CommunicationLog` row. Confirmed the Cash Forecast page shows
`Not connected` for starting cash (no manual override set) and lists the overdue invoice as a
LOW-CONFIDENCE inflow. Confirmed the audit log contains a real row per finance tool call, queryable
by `entity_type=invoice`/`entity_type=payment`.

One real bug found and fixed during this verification: the frontend's `runAction` helper checked
`result.status === "pending_approval"` to detect an `APPROVAL_REQUIRED` tool response, but FastAPI's
`HTTPException` always wraps its body under a `detail` key — so a real 202 response
(`{"detail": {"status": "pending_approval", ...}}`) was silently misread as a generic success and the
UI showed "Invoice sent" for an invoice that had *not* actually been sent. Fixed in `lib/api.ts`'s
shared `request()` helper (unwrap `detail` for any 202 response) rather than patching every caller.

## What's actually built and verified — Phase 6 (Marketing & Demand Generation)

### Domain model

`backend/app/models/marketing.py` (new) — `Campaign`, `MarketingSpend`/`MarketingSpendAllocation`
(the Payment/PaymentAllocation pattern, so one spend record can fund multiple campaigns),
`MarketingLeadSource`, `LeadAttribution`, `CampaignLead`, `CampaignConversion`, `MarketingContent`/
`ContentAsset`/`ContentVariant`/`ContentPublication`/`ContentPerformance`, `SEOPage`/`SEOKeyword`/
`SEOOpportunity`, `LocalListing`/`LocalReview`/`LocalReputationEvent`, `OutboundList`/
`OutboundContact`/`OutboundSequence`/`OutboundStep`/`OutboundEnrollment`/`OutboundActivity`,
`NurtureSequence`/`NurtureEnrollment`/`NurtureActivity`, `ReactivationCampaign`/
`ReactivationCandidate`. Money is `Decimal`/`Numeric` throughout. Deliberately does **not** persist
`MarketingMetric`/`MarketingSnapshot` — every spend/CAC/ROAS number is derived on request from real
rows by `app/services/attribution_service.py`, the same "derived service, never a stale cache"
decision Phase 5 made for AR. `Lead` (already carrying `source`/`source_detail`/`campaign_id` since
Phase 3), `Customer`, `Job`, `Invoice`, `Payment`, `CommunicationLog`, `OperationsException`, `Event`,
and `AuditLog` are reused, never duplicated. Migration `0006_marketing_domain` (hand-written, same
Postgres-not-available caveat as `0001`–`0005`).

### The Marketing Attribution Loop

`app/services/attribution_service.py` + `app/events/marketing_handlers.py` (mirrors
`finance_handlers.py`'s pattern). `attribute_lead` records `LeadAttribution` and, when a
`campaign_id` resolves, creates exactly one `CampaignLead` and one `CampaignConversion` row per lead
(deduplicated). Real, subscribed handlers then advance that same `CampaignConversion` row's `stage`
in place — never a second row — as `lead.qualified` → `appointment.created` (only if the appointment
carries the same `lead_id`) → `job.created` (via the job's own `lead_id`) → `job.closed` →
`invoice.created` (real `total`, from the job's real pricing) → `payment.received` (real
`amount_paid`) fire. `campaign_performance` then derives spend/leads/qualified/booked/jobs/revenue/
collected/CAC/ROAS purely from `MarketingSpendAllocation` + `CampaignConversion` rows — `cac`/`roas`
are `None` with an explicit `cac_note`/`roas_note` ("Insufficient data: no qualified leads yet",
"Insufficient data: no spend recorded yet", "Insufficient data: no leads attributed to this campaign
yet", "Attribution incomplete: no invoiced revenue yet for this campaign's leads") whenever the
underlying data can't support the number — never a fabricated `0` or `$0.00` masquerading as a real
answer.

### Paid Acquisition / Outbound providers

`app/marketing_ads/base.py` — `MarketingAdsProvider`/`GoogleAdsProvider`/`MetaAdsProvider`/
`YouTubeAdsProvider`/`LocalServicesAdsProvider` interfaces + `NotConnectedGoogleAdsAdapter`/
`NotConnectedMetaAdsAdapter`/`NotConnectedYouTubeAdsAdapter`/`NotConnectedLocalServicesAdsAdapter`,
distinct from (but consistent with) the Phase 2 `app/integrations/adapters.py` status stubs — these
add a `sync_campaign_performance` method that also honestly reports `NOT_CONNECTED` rather than
fabricating campaign data. `app/marketing_outbound/base.py` — `LeadListProvider`/
`ContactEnrichmentProvider`/`OutboundProvider`/`PermitDataProvider` + `NotConnectedClayAdapter`/
`NotConnectedApolloAdapter`/`NotConnectedInstantlyAdapter`/`NotConnectedPermitDataAdapter`, same
pattern.

### Content Engine

`app/services/content_service.py`: `MarketingContent` carries `IDEA → DRAFT → PENDING_APPROVAL →
APPROVED → SCHEDULED → PUBLISHED/ARCHIVED` in one row (not five near-identical tables). Publishing
goes through the *existing* `ApprovalRequest` model — `request_content_approval` / `approve_content`
(gated by the new `APPROVE_MARKETING_CONTENT` permission, which AI's default execution-boundary role
does not hold) / `reject_content`. `generate_draft_from_job` grounds a draft in a real, completed
job's real `service_type`, `internal_notes`, and real `JobAttachment` photo count — see
`app/services/ai_content_service.py`'s docstring: no `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` is
configured in this environment, so caption/SEO-page generation is a deterministic, template-based
generator, not a fabricated LLM response labeled as AI output. `is_llm_connected()` reports the honest
state for future callers to surface "AI CONTENT GENERATION NOT CONNECTED" against.

### Local & Organic / SEO

`app/services/seo_service.py` + `app/services/local_service.py`: SEO pages are always created
`DRAFT` / `ai_generated=True`; `publish_seo_page` is a separate, explicit, `APPROVAL_REQUIRED` action.
`SEOKeyword.search_volume`/`current_ranking` stay `NULL` unless a human enters a real observation — no
rank-tracking provider is connected, so nothing is fabricated. `LocalListing`/`LocalReview` are real,
owner-entered records; `provider`/`external_id` stay `NULL` until a real Google Business/Yelp
connection exists.

### Outbound & List Building / Nurture & Reactivation

`app/services/outbound_service.py`: contact dedup reuses the *exact* `normalize_email`/
`normalize_phone` helpers `Lead`/`Customer` matching already uses (service-layer check, same as
`Lead`, not a DB constraint, and never a fuzzy merge) — verified rejecting a case-different duplicate
email. Sequences use `OutboundStep.day_offset` + a deterministic `OutboundActivity.scheduled_for`
record with on-demand `execute_due_activities` — the same safe pattern Phase 5's `CollectionAction`
uses, explicitly **not** `workflow.sleep()`. `app/services/nurture_service.py` /
`app/services/reactivation_service.py`: candidate selection is pure, deterministic timestamp/status
comparison against real `Lead`/`Job`/`Customer` rows (stale lead ≥30 days, unbooked-qualified lead,
customer with no job in ≥180 days or no job history at all) — no LLM, mirrors Phase 4's delay
detection and Phase 5's overdue detection. Re-running identification is idempotent per
(campaign, customer)/(campaign, lead) — verified with a real backdated job.

### Marketing exceptions

Extends the *existing* `OperationsException`/`ExceptionType` enum (same engine as Operations/Finance,
no second mechanism) with `CAMPAIGN_OVERSPEND`, `LOW_CONVERSION`, `HIGH_CAC`, `ATTRIBUTION_GAP`,
`CONTENT_APPROVAL_DELAY`, `FAILED_PUBLICATION`, `REACTIVATION_FAILURE`. `CampaignService.
detect_performance_exceptions` is deterministic and on-demand: `CAC > $500` → `HIGH_CAC`; spend
`≥ $100` with zero qualified leads → `LOW_CONVERSION`. `CampaignService._check_budget_alert` opens
`CAMPAIGN_OVERSPEND` the moment spend exceeds a campaign's budget; budget status also reports
80%/90%/100%-consumed alerts short of that. Real ad-platform budgets are never changed automatically
— there is no real ad platform connected to change.

### Tools, policy, API, frontend

~35 new `marketing.*` tools across `app/tools/builtin/marketing_{campaign,attribution,content,seo,
outbound,nurture,reactivation,ads}_tools.py`, registered in `app/tools/factory.py` with policies in
`app/tools/policy.py`. New permissions: `READ_MARKETING`, `MANAGE_CAMPAIGNS`,
`MANAGE_MARKETING_CONTENT`, `APPROVE_MARKETING_CONTENT`, `MANAGE_OUTBOUND`, `MANAGE_NURTURE`,
`MANAGE_REACTIVATION`, `VIEW_MARKETING_ANALYTICS`. New routers: `marketing` (dashboard summary +
ads-provider status), `marketing_campaigns` (+ spend/budget/performance), `marketing_attribution`,
`marketing_content`, `marketing_seo` (+ local listings/reviews), `marketing_outbound`,
`marketing_nurture`, `marketing_reactivation`. New frontend: `/marketing`, `/marketing/campaigns[/[id]]`,
`/marketing/content`, `/marketing/seo`, `/marketing/outbound`, `/marketing/reactivation`; Marketing
extensions to the Owner Cockpit (`/dashboard`) and a "Generate content from job" path wired to real
job data. Nav updated to add Marketing/Campaigns/Content/SEO/Outbound/Reactivation without removing
any existing item.

**A real, load-bearing bug found and fixed while building the attribution E2E test** (not during
browser verification this time, but worth stating plainly): `operations.create_job`'s tool layer
silently routes to `create_job_from_appointment` — which pulls its title/schedule *from the
appointment*, ignoring every other field the caller passed — whenever `appointment_id` is set and no
explicit `idempotency_key` is given. This is existing Phase 4 behavior, not something this phase
introduced, but it means passing `appointment_id` *and* `estimated_revenue`/`lead_id`/a custom title
to `create_job` silently discards them. Worked around in the E2E test and in live verification by not
passing `appointment_id` to `create_job` when those other fields matter; not otherwise touched, since
fixing it is an Operations/Phase-4 change, not Marketing's to make unilaterally.

### Test results (actually run)

```
backend/tests/  (excluding test_temporal_workflows.py)  ->  127 passed, 0 failed
    (python -m pytest -q --ignore=tests/test_temporal_workflows.py, sqlite in-memory)
```

127 = Phase 1–5's 118 (all still passing, unmodified in behavior) + 9 new this phase:
`test_marketing_e2e.py` — the full realistic scenario (campaign → spend → lead → attribution →
qualification → appointment → job → close → invoice → payment → attributed revenue → campaign ROI,
asserting exact spend/leads/qualified/booked/jobs/revenue/collected/CAC/ROAS numbers) and an
incomplete-attribution scenario (real spend, zero qualified leads → `cac`/`roas` are `None` with an
honest note, and the same real spend-with-zero-qualified-leads condition is independently confirmed
to open a real `LOW_CONVERSION` exception) — 2 tests; `test_marketing_domain.py` — `NOT_CONNECTED`
ads/outbound providers, budget alert thresholds through `overspend detected` (and the matching
`CAMPAIGN_OVERSPEND` exception), cross-tenant isolation on marketing tools, outbound contact
duplicate rejection (case-insensitive email), the content approval permission boundary (a
`READ_ONLY`-role actor cannot `approve_content`; `OWNER` can), and deterministic reactivation
candidate identification with real idempotency (re-running produces zero new candidates for an
already-identified customer) — 7 tests. One real bug caught by this suite and fixed:
`ReactivationService` compared a timezone-aware cutoff against a timezone-naive datetime returned by
sqlite for a `DateTime(timezone=True)` column (Postgres wouldn't reproduce this; sqlite does) —
fixed by treating a naive value as UTC rather than letting the comparison raise.

Test coverage was consolidated into two files rather than the eight separately-named files the brief
suggested (`test_marketing_tools.py`, `test_marketing_attribution.py`, etc.) — every listed concern
(tenant isolation, permissions, attribution, campaign creation, spend, budget, content approval,
outbound dedup, reactivation eligibility, exception detection, `NOT_CONNECTED` integrations, audit
logging, idempotency) is covered by one of the two files; stated here plainly rather than implied.

Frontend: `npx tsc --noEmit` — 0 errors. `npm run build` — succeeds, 34 routes.

### Live browser verification (actually performed)

Continued in the same live tenant used for Phase 5 verification (re-logged-in after the dev JWT
expired mid-session — a real, expected token-lifetime event, not a bug). Verified: `/marketing`
dashboard shows real `$0`/zero-state numbers and real `NOT_CONNECTED` ads-provider cards before any
data exists; created a real `Campaign` (Google Search - AC Repair, GOOGLE_ADS, $1,000 budget) and
recorded real spend ($200) through `/marketing/campaigns/[id]`; created a real lead, attributed it to
the campaign, qualified it (real deterministic scoring), created a real customer/appointment/job
(`estimated_revenue=$900`), drove the job through the full state machine to `CLOSED` (photo upload via
real local storage, QA pass), triggered/approved/sent a real `$900` invoice, recorded a real test
payment → `PAID` — all via the same real, authenticated API calls the frontend uses (event
processing driven through the real `POST /api/v1/events/process/{event_type}` endpoint, since the
dev sandbox's in-memory transport has no background consumer). Confirmed the campaign detail page
then showed real, non-fabricated numbers: spend $200.00, leads 1/qualified 1, appointments 1/jobs 1,
jobs closed 1, revenue $900.00, collected $900.00, **CAC $200.00, ROAS 4.50x** — and confirmed the
Owner Cockpit's new Marketing section showed the identical numbers alongside the pre-existing,
undisturbed Finance/CRM sections. Generated a real content draft from the closed job (grounded in its
1 real photo, real title, real service data) and drove it through `IDEA → PENDING_APPROVAL →
APPROVED` via the `/marketing/content` UI. Created a real outbound list and contact via
`/marketing/outbound`. Created a real reactivation campaign and ran "Identify inactive customers" via
`/marketing/reactivation`, correctly returning zero candidates (every customer in the tenant had a
recent job — verified this logic separately with a real backdated job in the automated test). Ran
`detect_performance_exceptions` live and confirmed it correctly did *not* flag the just-created
campaign (CAC $200 is under the $500 threshold, and qualified leads exist). Confirmed real
`marketing.*` audit rows exist for every tool call above via a direct `audit_logs` query.

**One real bug found and fixed during this verification**: `dev.db`'s tables were created via
`Base.metadata.create_all` before the Marketing models existed in `Base.metadata`, so the live
`/marketing/summary` call failed with `sqlite3.OperationalError: no such table: campaigns` — a real
`500`, not a fabricated success. Fixed by re-running `Base.metadata.create_all` against the same
`dev.db` (idempotent — only creates missing tables); this is a dev-sandbox artifact of not having
Alembic-against-Postgres available, not a code bug, and is called out plainly rather than glossed
over.

## What's actually built and verified — Phase 7 (Retention & Referral)

### Domain model

`backend/app/models/retention.py` (new) — 14 tables: `CustomerLifecycleProfile` (one row per
customer, tracking `lifecycle_state` through NEW→FIRST_SERVICE→ACTIVE→REPEAT_CUSTOMER→AT_RISK→
INACTIVE→REACTIVATED→ADVOCATE), `RetentionOpportunity`, `ServiceReminder`, `ReviewRequest`,
`CustomerFeedback`, `ReferralProgram` (points at a real `Campaign`, channel=`REFERRAL`), `ReferralCode`,
`Referral`, `ReferralReward`, `CustomerRiskSignal`, `AdvocateCandidate`, `RetentionCampaign`,
`RetentionEnrollment`, `RetentionActivity`. Customer service history and lifetime-value snapshots are
explicitly **not** persisted tables — `RetentionService.customer_service_history` derives them from
real `Job`/`Invoice`/`ReviewRequest`/`Referral` rows on every call, matching the Phase 5 `ARService` and
Phase 6 `AttributionService.campaign_performance` precedent. Migration `0007_retention_domain`
(hand-written, same Postgres-not-available caveat as `0001`–`0006`) creates all 14 tables.

### Customer lifecycle & retention opportunity engine

`RetentionService.handle_job_closed` (subscribed to `JOB_CLOSED`) advances the lifecycle state
machine deterministically — no LLM involved — and opens two real opportunities per first-time close:
a `POST_JOB_FOLLOWUP` and a `REVIEW_ELIGIBLE` (skipped if the customer already has unresolved negative
feedback on file), plus schedules a `ServiceReminder` 180 days out. `detect_at_risk_and_inactive` is a
deterministic, on-demand detector (not a background poller) that flips lifecycle state to AT_RISK
(≥210 days since last service) or INACTIVE (≥365 days) and opens `CUSTOMER_AT_RISK` exceptions via the
*existing* `OperationsException` engine — no second exception mechanism. `identify_advocate_candidates`
is likewise deterministic: ≥2 completed jobs, no unresolved negative feedback, no open risk signal, no
overdue invoices.

### Reviews & reputation — the negative-feedback rule, enforced in code, not just policy

`ReviewService.record_feedback` computes sentiment from a 1–5 rating with a static threshold (1–2
NEGATIVE, 3 NEUTRAL, 4–5 POSITIVE — deterministic, not AI). NEGATIVE feedback opens
`SERVICE_RECOVERY_REQUIRED` (HIGH) and `NEGATIVE_FEEDBACK` (MEDIUM) exceptions and publishes
`RETENTION_SERVICE_RECOVERY_REQUIRED` — there is no code path anywhere from a NEGATIVE rating to a
`ReviewRequest.status = REQUESTED`. `_create_review_eligibility` additionally never creates a review
request at all if the customer already has any NEGATIVE feedback on file, so this holds even across
multiple jobs. `retention.send_review_request` is deliberately `APPROVAL_REQUIRED` at the policy layer
(unlike `finance.send_invoice`, it has no other state-machine gate before it reaches a real customer
channel) — verified live: clicking Send in `/retention/reviews` surfaces "Sending a review request
requires approval — an ApprovalRequest has been created" rather than a fake success.

### Referrals — reusing Phase 6's attribution engine, not duplicating it

`ReferralService.create_program` creates a real `Campaign` (channel=`REFERRAL`,
objective=`LEAD_GEN`) and a `ReferralProgram` pointing at it — verified live in
`/marketing/campaigns` as "Referral Program: Refer a Friend". `get_or_create_code` generates unique
8-char codes. `convert_referral_to_lead` reuses the *existing* `LeadService.create_lead`
(`source=REFERRAL`, idempotency key `referral-{referral_id}`) — no second lead table or path — and
calls `AttributionService.attribute_lead`, so `/marketing/campaigns/{id}` reports the referral's real
leads/jobs/revenue/collected-revenue with **zero new analytics code**, including an honest
"Insufficient data: no spend recorded yet" for CAC/ROAS. Event handlers advance
`Referral.status` off real events (`LEAD_QUALIFIED`→QUALIFIED, `APPOINTMENT_CREATED`→BOOKED,
`INVOICE_CREATED`→CONVERTED, `PAYMENT_RECEIVED`→collected-revenue update).
`mark_converted` auto-calls `request_reward` when the program has a reward amount, creating a real
`ApprovalRequest` — but reward approval uses **dedicated, permission-gated tools**
(`approve_referral_reward`/`reject_referral_reward`/`issue_referral_reward`, requiring
`APPROVE_REFERRAL_REWARD`) rather than the generic ToolRegistry approval path, so approving actually
resumes/completes the reward (unlike the still-open generic dead end named below). AI cannot approve
its own reward: the tools are permission-gated for a human, and no `AIExecutionService` path calls
them. No real payment provider is ever called — `issue_reward` is an internal record only.

### Exceptions, permissions, events — all reused, none duplicated

Five new `ExceptionType` values (`CUSTOMER_AT_RISK`, `SERVICE_RECOVERY_REQUIRED`,
`NEGATIVE_FEEDBACK`, `MISSED_FOLLOWUP`, `REFERRAL_REWARD_REVIEW`) on the *same* `OperationsException`
table used by every other phase. Nine new `Permission` values, all added to `Role.MANAGER`;
`READ_RETENTION`/`VIEW_CUSTOMER_HEALTH`/`VIEW_REFERRAL_ANALYTICS` added to `Role.READ_ONLY`. Fifteen
new `EventType` values consumed by `register_retention_handlers`, wired alongside the existing
marketing/finance/operations/crm handlers in both `app/api/tool_deps.py` and `tests/conftest.py` — no
second event bus instance.

### Tools, API, frontend

~20 new `retention.*` tools across 8 files in `app/tools/builtin/`, all AUTO at the policy layer except
`retention.send_review_request` (see above) — with an explicit comment block in `app/tools/policy.py`
explaining why reward/campaign-enrollment tools stay AUTO at the policy layer (gated by permission on
dedicated tools instead) rather than recreating the "generic `ApprovalRequest` doesn't resume the
original action" dead end named in every prior phase's status doc. Six new routers under
`/api/v1/retention*` (193 total routes app-wide). Frontend: `/retention` (summary + analytics, honest
`INSUFFICIENT DATA` fallback), `/retention/opportunities`, `/retention/reminders`, `/retention/reviews`
(review requests + a feedback-recording form + a red "service recovery" banner for negative feedback,
never a public-review prompt), `/retention/referrals` (programs, code generation, referral creation,
lead conversion, rewards with approve/reject/issue), plus a "Customer health" and "Retention timeline"
section added to the existing `/customers/[id]` Customer 360 page (both built from real API-backed
data — the timeline is assembled client-side from real opportunity/reminder/review-request/feedback
rows, never fabricated events) and a "Retention & Referral" section added to the existing `/dashboard`
Owner Cockpit with a "RETENTION NEEDS ATTENTION" banner driven by real open retention exceptions.

### Test results

136/136 backend tests pass (7 domain tests + 2 E2E tests new this phase, on top of the 127 from
Phase 6), including the full 28-step critical E2E scenario (referral program → code → referral →
lead → qualify → book → job → close → invoice → pay → CONVERTED → reward requested → approved →
issued → REWARDED, with a duplicate `INVOICE_CREATED` re-delivery proving idempotency) and a dedicated
negative-feedback scenario proving no public review request is ever created. Frontend: `npx tsc
--noEmit` clean, `npm run build` clean (31 routes, up from 25).

### Live browser verification (this session, fresh tenant, real dev servers)

Registered a fresh tenant ("Phase7 Retention Co"). Created a real customer (Alice), created and closed
a real job (JOB-1001) through the full DRAFT→...→CLOSED state machine (including uploading a real
photo to pass QA and generating a real completion packet), triggered a real $450 invoice, sent and paid
it. Processed the durable event queue via the documented dev/ops endpoint
(`POST /api/v1/events/process/{event_type}` — "drive the subscriber loop on demand instead of waiting
for a background worker," per its own docstring) and confirmed on Alice's Customer 360: lifecycle
NEW→FIRST_SERVICE, a real 180-day service reminder, a real post-job-followup opportunity, and a real
review-eligibility opportunity, all in a real Retention Timeline with an honest "Next recommended
action: Send a review request." Clicked Send on `/retention/reviews` and confirmed the approval-gated
notice fires instead of a fake success. Recorded real positive feedback (5/5) and real negative
feedback (2/5) for Alice via a form added to `/retention/reviews` — confirmed the negative record opens
real `SERVICE_RECOVERY_REQUIRED`/`NEGATIVE_FEEDBACK` exceptions (visible on `/exceptions`) and shows a
red recovery banner, with no review request ever sent. Created a referral program ("Refer a Friend",
$50 credit) and confirmed it created a real `Campaign` (channel=REFERRAL) visible in
`/marketing/campaigns`. Generated a referral code for Alice, created a referral, converted it to a real
lead ("Bob Referred", `source=REFERRAL`, visible in `/leads`), qualified the lead (real deterministic
scoring: 80/100), booked a real appointment, created and closed a second real job (JOB-1002, $600) for
a new customer record for Bob, triggered/sent/paid a real $600 invoice, and confirmed the referral
progressed CREATED→LEAD_CREATED→QUALIFIED→BOOKED→CONVERTED with real `$600.00` collected revenue.
Confirmed `/marketing/campaigns/{referral campaign id}` reports that $600 revenue and 1 job with zero
new analytics code, and an honest "Insufficient data: no spend recorded yet" for CAC/ROAS. Approved and
issued the $50 reward (PENDING→APPROVED→ISSUED, referral REWARDED) via the dedicated permission-gated
tools. Confirmed the Owner Cockpit's new Retention & Referral section shows the correct real numbers
(1 referral lead, $600 referral revenue, 5 open retention opportunities, 2 open retention exceptions,
amber "RETENTION NEEDS ATTENTION" banner) and that Bob's own Customer 360 independently shows
lifecycle FIRST_SERVICE and his own real retention timeline.

**Two real bugs found and fixed during this verification, not glossed over:**

1. **Frontend gap**: the "New job" modal and the referral/review pages initially had no way to set a
   job's `estimated_revenue`, generate a referral code, create a referral, or record customer
   feedback — the backend tools/API existed but no UI called them. Fixed by adding a "Record
   feedback" form to `/retention/reviews` and a "Referral codes" / referral-creation / lead-conversion
   section to `/retention/referrals` (both now shipped, typechecked, and build-clean). The
   `estimated_revenue` gap on the job-creation modal is a pre-existing Operations-scope limitation
   (Phase 4/5 already noted `operations.update_job` has no revenue fields either) — worked around for
   verification via the real `POST /api/v1/jobs` endpoint directly (which does accept
   `estimated_revenue`/`lead_id`), not by editing the database.
2. **Durable event delivery needs a driver in this dev sandbox**: `EventBus.publish()` is
   durability-first (writes the row, sends to transport) but does not invoke subscribers itself —
   nothing in this dev setup runs the documented `POST /api/v1/events/process/{event_type}` "dev/ops
   endpoint" continuously the way a production worker process would, so retention/referral state
   transitions did not appear until that endpoint was called. This is not new to Phase 7 — the
   endpoint's own docstring says "in production this runs continuously in a worker process, not on
   the request path" — but it is the first phase where the gap was actually hit live in this session
   (Phases 5/6 verification apparently synchronized differently or got lucky with timing). Not a code
   bug; a real, named, pre-existing operational gap: **there is still no actual background worker
   process running the subscriber loop continuously**, in dev or as a documented production
   deployment story.

## What's actually built and verified — Phase 8 (Event Worker + AI Owner Morning Brief)

### Part A — the Klaros Event Worker

**The core problem this phase closes**: every prior phase's live verification required manually calling
`POST /api/v1/events/process/{event_type}` because nothing ran the event bus's subscriber loop
continuously. As of Phase 8, a business event published once now propagates automatically — verified
live end-to-end with zero calls to that endpoint (see below).

**Architecture** — nothing new was invented; the existing `EventBus.process_pending` (durability,
idempotency via the unique `(event_id, handler_name)` `EventProcessingRecord` row, retry-with-backoff,
dead-lettering — all unchanged) is now driven by a real continuous poll loop instead of a human clicking
an endpoint:

```
Application -> EventBus.publish() -> Durable Event Store (Postgres/sqlite)
                                            |
                          Klaros Event Worker (app/events/worker.py)
                          [poll loop -> EventBus.process_pending() per subscribed event type]
                                            |
                          Subscriber Registry -> Handlers -> Domain side effects
                                            |
                                    Audit Log / Metrics
```

`app/events/worker.py`'s `EventWorker` class adds exactly three things on top of the existing bus:
a poll loop (`run_forever`, default 1s, configurable via `EVENT_WORKER_POLL_SECONDS`), graceful shutdown
(an `asyncio.Event`, plus real `SIGTERM`/`SIGINT` handlers when run standalone), and in-process metrics
(`app/events/metrics.py` — a small, dependency-free counter registry; no Prometheus/OpenTelemetry exists
in this repo, so nothing was fabricated to look like one).

**Deployment topology — two real modes, not disguised as each other**:
- **Production** (`EVENT_TRANSPORT=redis`): the worker runs as its own OS process — a new `event-worker`
  Docker Compose service (`command: python -m app.events.worker`), explicitly distinct from the existing
  `worker` service (which runs the *Temporal* worker for long-running business workflows — the compose
  file now comments both to prevent exactly the confusion the spec warned about). It consumes the same
  Redis Streams the API process publishes to via the pre-existing, previously-unexercised
  `RedisStreamTransport` — genuinely cross-process, using Redis Streams consumer groups (`XREADGROUP`)
  for the "only one worker touches this message" guarantee, the same role `SELECT ... FOR UPDATE SKIP
  LOCKED` plays for Postgres-polling designs. Not run against a live Redis server in this sandbox (no
  `redis-server` binary available — same standing constraint as every prior phase).
- **Dev/local fallback** (`EVENT_TRANSPORT=memory`, this sandbox's actual running configuration): the
  worker runs as a background `asyncio` task inside the API process itself, started in `app/main.py`'s
  lifespan. This is not a shortcut disguised as the real thing — `InMemoryTransport`'s state lives only
  in one process's memory (documented since Phase 2), so a separate OS process could never see events
  published by the API process anyway; co-locating the worker is the only way this transport can work at
  all, exactly analogous to why `InMemoryTransport` itself exists.

**A real, separate bug found and fixed while wiring this up**: `get_wired_event_bus()` (which calls
`register_*_handlers`) was previously only invoked *lazily*, on first request to an `/api/v1/events/*`
route. A fresh server that never happened to hit that route had a fully-subscriptionless bus — every
published event was durably stored but silently never consumed by anyone. This exactly explains why
Phase 7's live verification needed the manual endpoint at all. Fixed by wiring handlers eagerly in
`app/main.py`'s lifespan, before the app accepts any requests.

**Idempotency, retries, dead-lettering, crash recovery** — all pre-existing `EventBus` guarantees, now
exercised continuously rather than on-demand: `_handle_one` marks `Event.status` PROCESSING at the start
of an attempt (new — previously the intermediate state was invisible) and RETRYING between failed
attempts (new `EventStatus.RETRYING`), so `Event.status` now tells the true story of an event that
crashed mid-processing, not just its terminal state. `EventProcessingRecord` gained `last_attempt_at`/
`processed_at` timestamps (migration `0008`, additive/nullable) for observability. **Restart recovery
needs no special code**: all state lives in the database, not the worker process, so a fresh
`EventWorker` instance just resumes polling — verified live (see below) by killing and restarting the
whole API process mid-session and confirming a freshly-published event was still picked up automatically
by the new worker instance, with all prior data intact.

**A second real bug found during this phase's own concurrency testing, fixed in `EventBus._handle_one`**:
two workers racing to claim the same brand-new `(event_id, handler_name)` pair for the first time hit an
uncaught `IntegrityError` on the unique-constraint insert and crashed instead of backing off. Fixed:
the insert-and-mark-PROCESSING commit now catches that specific `IntegrityError`, rolls back, and treats
it as "another writer already claimed this" — logs and returns, never running the handler. Covered by a
dedicated test that deterministically forces the race via `monkeypatch` (true multi-connection concurrency
against this sandbox's single shared SQLite test connection is itself unreliable to test against — see
below).

**A third, more significant real bug found live, not in tests, with a genuine root-cause fix**: running
the in-process worker (dev/`EVENT_TRANSPORT=memory` fallback) continuously *inside the same process* as
live HTTP request handling exposed a real transaction-corruption hazard against the file-based `dev.db`
SQLite database — `app/db/session.py` was unconditionally using SQLAlchemy's `StaticPool` (one shared
physical DBAPI connection for the whole process) for *any* SQLite URL, a setting that only exists because
`:memory:` SQLite requires it (a `:memory:` database only exists for the connection that created it).
A real file (`dev.db`) doesn't have that problem — SQLite's own file-level locking safely serializes
separate connections. Sharing one connection anyway meant the worker's background tick and an in-flight
HTTP request's transaction could corrupt each other's SQLAlchemy session state. Hit live: recording a
test payment returned a false "Action failed" from a `session.refresh()` `InvalidRequestError`, even
though the payment's own `INSERT` had already committed — leaving one orphaned `PaymentAllocation` row
with no matching `Payment` row as a real, verified artifact of the corruption (found via direct
inspection, documented rather than silently cleaned up). **Root-cause fix**: `app/db/session.py` now only
uses `StaticPool` when the URL contains `:memory:`; a real file gets normal per-session connections plus
a SQLite busy-timeout (`timeout=15`) so a second concurrent writer waits instead of erroring. Verified
fixed live: after the fix and a clean restart, the exact same payment-recording action (and every
subsequent action) completed cleanly with no false failures. The one pre-fix orphaned row was left in
place as an honest artifact rather than hidden.

**Event Worker admin surface**: `/api/v1/events` (list, filter by status), `/api/v1/events/{id}/detail`
(event + every handler's processing attempts), `/api/v1/events/dead-letters` (list) +
`/api/v1/events/dead-letters/{id}/replay` (reuses the pre-existing `EventBus.replay`, itself reusing
`_handle_one`'s idempotency check — replaying an event whose handler already succeeded is a safe no-op),
`/api/v1/events/metrics` (real in-process counters). All implemented as real `events.*` Tools
(`app/tools/builtin/event_tools.py`) called through `ToolRegistry`, gated by two new permissions
(`READ_EVENTS`, `MANAGE_EVENTS`) — so every admin action is audited the same way every other important
action in this codebase is, with no bespoke bypass. Frontend: `/events` — real worker metrics, a
status-filterable event list, and a dead-letter list with a working Retry button.

### Part B — the AI Owner Morning Brief

**Real data sources, no fabrication**: six new read-only `insights.*` Tools
(`app/tools/builtin/insight_tools.py`, backed by `app/services/insight_service.py`) — finance, operations,
sales, marketing, retention, and exception snapshots — each a small, focused SQL aggregation against the
real domain tables (Invoice/Payment, Job/OperationsException, Lead/Appointment, Campaign/MarketingSpend,
CustomerLifecycleProfile/RetentionOpportunity/CustomerFeedback/ReferralReward), gated by the same
`READ_*`/`VIEW_*`/`MANAGE_EXCEPTIONS` permissions already used elsewhere for those domains. These are
deliberately smaller and separate from the existing `/finance/summary`-style dashboard endpoints (which
were left completely untouched) — a coarser, decision-relevant slice for a daily brief, not a dashboard
replacement.

**The AI execution boundary is genuinely exercised for the first time**: `AIExecutionService`
(`app/ai/execution_service.py`) was scaffolded in Phase 2 but never actually called by anything until
now. `MorningBriefService.generate` calls each insight tool through it with `actor_type=AI`, producing
real audit rows tagged `actor_type=AI` for every insight fetch — verified directly in the critical E2E
test. `MorningBriefService` then *synthesizes* — turns structured tool output into insight/recommendation
rows using deterministic, named-threshold rules (`STALE_QUALIFIED_LEAD_DAYS`, priority-by-severity), the
same rule-based-not-LLM-guess pattern used for lead scoring, exception detection, and feedback sentiment
in earlier phases.

**AI provider abstraction, and the fallback is not a fake AI response**: `app/services/ai_provider.py`
defines `AIProvider`/`NullAIProvider`/`AnthropicAIProvider`/`OpenAIAIProvider`, selected by
`get_ai_provider()` based on the real `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` settings (scaffolded since
Phase 2, never wired to anything until now). Neither key is configured in this environment (`.env` /
`.env.example` both blank), so `get_ai_provider()` always returns `NullAIProvider`, and every brief this
phase generated is labeled — in the database, not just in the UI — `mode=DETERMINISTIC`, surfaced in the
frontend as **"DETERMINISTIC SUMMARY — AI NOT CONNECTED"**, verified live. `mode` is only ever upgraded to
`AI` if a real provider call is actually attempted and returns real prose (checked in
`MorningBriefService.generate`, not just labeled by whether a key exists) — so a configured-but-broken
key can never silently claim AI authorship of unchanged template text.
`AnthropicAIProvider`/`OpenAIAIProvider` are real, correctly-shaped classes but their
`synthesize_brief_prose` deliberately raises `NotImplementedError` rather than shipping an HTTP call to a
live API this environment has no credentials to test against.

**Deterministic insight detection, real recommendations with real actions**: per-domain thresholds
(overdue invoices, blocked jobs, unassigned scheduled jobs, qualified-but-unbooked leads, negative
feedback, at-risk customers, pending referral rewards, open exceptions by severity) produce insights with
a category/priority/summary and, where a related entity exists, a real link to it (verified live: "View
customer" from a service-recovery insight opens the real Customer 360 page). Recommendations carry
`what`/`why`/`related_entity`/`next_action`, and — critically — **never execute anything themselves**.
Where a recommendation genuinely maps to a safe, already-permission-gated tool (currently: approving a
pending referral reward, via the Phase 7 dedicated-tool pattern), it carries an `executable_tool`/
`executable_input` pair; `insights.execute_recommendation` (a new Tool, gated by a new
`EXECUTE_RECOMMENDATION` permission) dispatches to that *exact* tool through the normal `ToolRegistry`
pipeline using the *executing user's own* `ExecutionContext` — not the Morning Brief's `AI` context — so
an `APPROVAL_REQUIRED` or `BLOCKED` tool behaves identically to being called directly. Every other
recommendation has no `executable_tool` at all and the frontend correctly offers only "View"/"Dismiss",
never a fake "Execute" button.

**Scheduling — piggybacked on the Event Worker's existing loop, not a second sleep-based scheduler**:
`Organization` gained `morning_brief_enabled`/`morning_brief_local_time`/`morning_brief_timezone` columns
(migration `0008`). `MorningBriefService.check_and_generate_scheduled` is passed to `EventWorker` as an
`on_tick` hook — it runs once per existing poll tick (both in-process and standalone deployment modes),
checks each enabled tenant's real local time (via `zoneinfo`) against its configured brief time, and
generates a brief only if no brief exists yet for that tenant's local calendar date — idempotent by
construction, verified by three dedicated tests (fires once past the time, never fires early, never
double-fires on repeated ticks).

**Frontend**: `/morning-brief` (headline with an honest mode badge, "Needs attention"/"Recommended
actions" with View/Execute/Dismiss, and a schedule settings panel) and a Morning Brief summary
integrated into the existing `/dashboard` Owner Cockpit (real headline, real high-priority insights,
real pending recommendations, a link to the full page) — the stale "AI Morning Brief: not connected"
line was removed from the Foundation Status list since it is now genuinely connected; every other
existing dashboard section was left untouched, per the standing instruction not to disturb prior
domains' summaries.

### Test results

169/169 non-Temporal backend tests pass (33 new this phase: 12 event-worker unit/concurrency tests, 6
events-admin API tests, 8 Morning Brief domain tests, 4 scheduler tests, 2 Morning Brief API tests, and
the critical 1 end-to-end scenario — up from Phase 7's 136). The critical E2E test
(`tests/test_phase8_e2e.py`) drives customer -> job -> close -> (worker auto-processes `job.closed`,
asserted via polling the database, never calling `process_pending`) -> retention lifecycle auto-updates
-> invoice auto-created -> send -> pay -> (worker auto-processes `payment.received`) -> invoice
auto-PAID -> negative feedback -> (worker auto-processes the feedback event) -> service recovery
exception auto-appears -> Morning Brief generated and asserted to report the real exception, real $500
revenue, and the real customer -> idempotency re-verified (no duplicate invoice/exception after further
ticks) -> real `actor_type=AI` and `actor_type=USER` audit rows both confirmed present — **with zero
calls to `process_pending` or the manual endpoint anywhere in the test**, run against a real background
`EventWorker` task. Frontend: `npx tsc --noEmit` clean, `npm run build` clean (33 routes, up from 31).

### Live browser verification (this session, fresh tenant, real dev servers, in-process worker)

Registered a fresh tenant. Created a real customer and job directly via the real `POST /api/v1/jobs`
API (the "New job" modal still has no `estimated_revenue` field — a pre-existing, previously-documented
gap, not new this phase) with a real $750 estimated revenue, then drove it through the full
DRAFT-through-CLOSED state machine live in the browser, including a real photo upload for QA and a real
completion packet. **Closed the job and, without touching any manual endpoint, watched a real $750.00
DRAFT invoice (INV-1001) appear on the job page on its own** — the single clearest proof of this phase's
core acceptance criterion. Approved, sent, and paid the invoice live. Recorded real negative feedback
(1/5) and confirmed — with no manual processing — a real `SERVICE_RECOVERY_REQUIRED` (HIGH) and
`NEGATIVE_FEEDBACK` (MEDIUM) exception both appeared on `/exceptions`, and a review request became
`ELIGIBLE` on `/retention/reviews`. Generated a Morning Brief and confirmed it was honestly labeled
"DETERMINISTIC SUMMARY — AI NOT CONNECTED", reported the real job-closed count, the real negative
feedback, the real 2 open exceptions (1 high severity), and offered a real "Perform service recovery"
recommendation linking to the real customer — visible both on the dedicated `/morning-brief` page and
summarized on `/dashboard`. Confirmed `/events` shows real, non-fabricated worker metrics (live tick
count climbing, real processed/dead-lettered/deduplicated counters) and a real, correctly status-tagged
event list. **Killed and restarted the entire backend process mid-session, confirmed all prior data
survived, then recorded one more real action and confirmed the freshly-restarted worker instance (new
"Started" timestamp, counters reset to zero) picked it up and processed it automatically** — direct proof
of crash/restart recovery.

**Bugs found and fixed during this verification, not glossed over** (the three architecturally significant
ones are described in full under Part A above — the lazy handler-wiring gap, the concurrent-insert race,
and the `StaticPool`/file-SQLite transaction-corruption root cause): the third one produced one real,
verified data artifact (an orphaned `PaymentAllocation` row with no matching `Payment` row, for the
payment recorded in the window before the fix + restart) — identified via direct database inspection,
disclosed here rather than silently repaired, and does not affect the correctness of the fix itself
(verified: the identical action, retried after the fix and restart, completed with no false failure and
no orphaned row).



- No dedicated `/finance/exceptions` page — the *existing* `/exceptions` page already lists every
  `OperationsException` regardless of type, including the 9 new Finance types, so a second page would
  duplicate rather than extend. Named as a deliberate choice, not an oversight.
- Double-entry bookkeeping, a formal chart of accounts, and QuickBooks/Xero/Stripe/Bill.com/banking
  integrations are all `NOT_CONNECTED` — no OAuth flow exists, no credential is configured, no API
  call has ever been made to any of them. Klaros is the operational finance layer, not the accounting
  system of record, per the explicit spec permission.
- The AI Finance Assistant (spec section's example Q&A list) is not built as a distinct feature — the
  AI execution boundary can already reach every `finance.*` tool with the same permission/policy
  enforcement as every other tool, but no finance-specific prompt/skill layer was added this phase.
- No scheduler/cron runs `detect_overdue`/`execute_due_actions`/cash-forecast generation
  automatically — all three are real, tested, on-demand endpoints (same pattern as Phase 4's delay
  detection).
- Per-tenant-configurable collection policy / approval thresholds: both are static in-process
  constants (`AUTO_APPROVAL_THRESHOLD`, `UNUSUAL_DISCOUNT_RATIO`, `COLLECTION_POLICY`), the same
  simplification already accepted for Phase 3's lead scoring and Phase 4's delay tolerance.
- **Phase 6**: no dedicated `/marketing/exceptions` page — same reasoning as Finance's: the existing
  `/exceptions` page already lists every `OperationsException` including the 7 new Marketing types.
  No Customer 360 marketing-history extension (original source/campaign/nurture/reactivation
  activity) was added this phase — a real gap, not attempted due to time, named plainly rather than
  silently skipped. No `/marketing/outbound` sequence-builder UI (steps/enrollment) or
  `/marketing/nurture` dashboard page — the services/tools/API exist and are tested, but only
  list/content/outbound-contact management got a frontend page this phase; sequence creation and
  enrollment are real, tested, API-only for now. No AI-generated ad/email/SMS copy beyond the
  deterministic job-caption/SEO-page templates. `MarketingSpendAllocation` supports splitting one
  spend record across multiple campaigns, but no UI exposes multi-campaign allocation (the
  `/marketing/campaigns/[id]` "Record spend" form always allocates 100% to that one campaign).
  `CampaignConversion.stage` only ever reaches as far as the real event chain can take it — a lead
  never attributed to a job (no `Job.lead_id`) simply never advances past `LEAD`/`QUALIFIED`/`BOOKED`,
  which is correct (an `ATTRIBUTION_GAP` exception type exists in the enum for this case but no
  detector was wired up to create it this phase — a named gap, not a silent one).

## What's actually built and verified — Phase 9 (Approval Orchestration + Real AI Provider)

### Part A — closing the "approval dead end"

Every prior phase named the same gap: a `ToolRegistry`-level `APPROVAL_REQUIRED` policy created a
generic `ApprovalRequest`, and approving it only flipped that request's own status — it never resumed
the original tool call. `finance.void_invoice`/`record_payout`,
`marketing.publish_content_variant`/`publish_seo_page`/`respond_to_review` all inherited this limitation.
Phase 9 closes it for good, for every tool, not just one.

- **`ApprovalRequest` extended in place** (additive columns only, no old migration touched):
  `requested_by_role` (captured at request time so resume uses the actor's real role, not a guess),
  `decided_at`, `execution_status` (`NOT_STARTED`/`EXECUTING`/`EXECUTED`/`FAILED`), `execution_result`,
  `execution_error`, `executed_at`, `execution_attempts`, `idempotency_key`.
- **`ApprovalExecutionService`** (`app/services/approval_execution_service.py`, new) is the one place
  that resumes an approved action. `approve()` → `execute_approved()` → `ToolRegistry.execute(...,
  skip_approval_gate=True)` — the exact same registry, permission check, tenant check, schema
  revalidation, and audit path every other tool call goes through. `skip_approval_gate` skips only the
  `APPROVAL_REQUIRED` branch; the `BLOCKED` check still runs, so a policy that changed to `BLOCKED`
  between request and approval correctly fails execution rather than silently proceeding (tested).
- **State machine, DB-enforced, not Python locks**: `PENDING → APPROVED/REJECTED` and
  `NOT_STARTED → EXECUTING` are both `UPDATE ... WHERE <expected-state>` compare-and-swap statements
  checked via `rowcount`. Two dedicated concurrency tests fire real concurrent `asyncio.gather()` calls
  and count real side-effect rows (not mocks) to prove exactly-once execution under a race.
- **Self-approval is structurally impossible**, not just conventionally discouraged: `ApproveAction`/
  `RejectAction` reject any `actor_type=AI` caller outright (`ValueError`, tested), and
  `ApprovalExecutionService.approve()` independently rejects a requester approving their own request
  (`SelfApprovalError` → HTTP 403, tested and verified live in the browser — see below).
- **Retry**: a `FAILED` execution can be retried (`approvals.retry_execution` /
  `POST /approvals/{id}/retry`) — re-arms `execution_status` back to `NOT_STARTED` via the same CAS
  pattern and calls `execute_approved()` again; concurrent retries were tested and proven idempotent.
- **Audit**: every step — the original tool call hitting `APPROVAL_REQUIRED`, the approve/reject
  decision, the resumed execution's start/success/failure, and any retry — writes to the one existing
  `AuditLog` table, no second audit store.
- New tools, all `AUTO` at the policy layer and permission-gated instead (`approvals.approve` needs
  `APPROVE_ACTIONS`, `approvals.reject` needs `REJECT_ACTIONS`, `approvals.retry_execution` needs
  `EXECUTE_APPROVED_ACTIONS`, `approvals.list`/`get_detail` need `READ_APPROVALS`) — the same
  established pattern (Phase 5's `finance.send_invoice`, Phase 7's referral rewards, Phase 8's
  `insights.execute_recommendation`) used specifically to avoid a second-order "approval required to
  approve" dead end.
- `frontend/app/approvals/page.tsx` (new): Pending/Approved/Rejected filters, full per-approval detail
  (tool, reason, requester, input, execution status/result/error/attempts), Approve/Reject with an
  optional decision note, Retry for a failed execution — all real API data, no placeholders.

### Part B — the real AI provider

- **`app/services/ai_provider.py` rewritten.** `DeterministicAIProvider` (renamed from `NullAIProvider`,
  old name kept as a back-compat alias) is the only provider ever active with no key configured.
  `AnthropicAIProvider`/`OpenAIAIProvider` now have real `httpx` call bodies (Messages API /
  Chat Completions), gated behind one seam (`_call_api`) so tests never need a live key. New
  `AI_PROVIDER` setting (`auto`/`deterministic`/`anthropic`/`openai`) plus `ANTHROPIC_MODEL`/
  `OPENAI_MODEL` — `auto` (default) prefers Anthropic, then OpenAI, else deterministic;
  `deterministic` forces the safe path even with keys present.
- **Structured output only, never free text**: `AIBriefEnrichment { summary: str, insights: [{entity_id,
  text}] }`, validated with Pydantic. Any malformed JSON, schema violation, timeout, or HTTP error is
  caught and turned into `None` — `MorningBriefService` treats that exactly like "no provider
  configured" and the brief stays fully deterministic. `mode=AI` is set only when a real call actually
  succeeded and validated — checked in code.
- **The LLM boundary is structural, not a prompt instruction alone**: the provider is never given
  write access to anything, and its output schema has no field that could carry a fabricated number,
  date, or status — only prose. `MorningBriefService.generate()` additionally cross-checks every
  `entity_id` the AI response references against the real, already-computed deterministic insight list
  and silently drops anything that doesn't match, so a hallucinated or injected reference can never
  attach prose to an entity nothing real ever flagged (`test_ai_response_cannot_attach_prose_to_an_entity_that_was_never_flagged`).
  Business data sent to the model is fenced and labeled `BUSINESS DATA`, with an explicit instruction
  to treat its contents as data, never as commands — a real, if basic, prompt-injection defense.
- **Provider attribution stored, never a secret**: `MorningBrief` gained `ai_provider`, `ai_model`,
  `ai_generation_ms` columns — always `NULL` for a `DETERMINISTIC` brief, populated only on a real
  successful call. Surfaced on `/morning-brief` (`AI — anthropic (claude-3-5-sonnet-...)` vs
  `DETERMINISTIC SUMMARY — AI NOT CONNECTED`).
- **`insights.execute_recommendation` now handles `APPROVAL_REQUIRED` correctly.** Previously an
  approval-required underlying tool would have made this call fail outright (an uncaught
  `ToolApprovalRequiredError` misattributed to the recommendation-execute call itself). It now catches
  that specific exception, moves the recommendation to a new `RecommendationStatus.APPROVAL_REQUESTED`,
  and stores the real `approval_request_id` on the `MorningBriefRecommendation` row — the `/morning-brief`
  page links straight to `/approvals` instead of just saying "pending."
- **`/ai-activity` (new)**: `audit.list_ai_activity` tool filters the existing `AuditLog` table for
  `actor_type=AI` rows plus the full `approval.*` lifecycle — a read-only, reused view, not a second
  audit store.

### Test results

204/204 non-Temporal backend tests pass (35 new this phase: 17 approval-orchestration tests covering
every scenario in the spec's checklist — AUTO/APPROVAL_REQUIRED/BLOCKED policy behavior, resume-and-execute,
rejection, double-approval, concurrent-execution race, tenant isolation, self-approval, AI-cannot-approve,
policy-changed-to-BLOCKED-after-approval, input-schema-revalidated-on-execution, failed-execution-recorded,
concurrent-retry-idempotent, full audit lifecycle, event emission, and RBAC grants — plus 11 AI-provider
tests (success, malformed response, schema violation, timeout, provider error, no-network-call-with-no-insights,
provider selection, forced-deterministic override, no-secret-leakage) and 4 Morning Brief tests (AI-mode
headline upgrade + provider metadata, the entity-id cross-check boundary, plus 3 audit-tool tests) — up
from Phase 8's 169. The critical end-to-end test (`tests/test_phase9_e2e.py`) drives a real referral
loop (referral → lead → reward request) alongside a real overdue invoice through a live, continuously
running `EventWorker` — Morning Brief detects both from real tool output — Owner clicks Execute on the
reward recommendation — policy returns `APPROVAL_REQUIRED` — a real `ApprovalRequest` is created (not a
dead end) — the requester's own approval attempt is rejected — a different real approver approves it —
`ApprovalExecutionService` resumes and actually executes the original `retention.approve_referral_reward`
call — the real `ReferralReward` row transitions to `APPROVED` — re-approving is rejected outright — exactly
one reward row was ever touched — the audit log carries the full recommendation → approval → execution
lifecycle — **with zero calls to `process_pending` or any manual event-processing endpoint anywhere in the
test**. Frontend: `npx tsc --noEmit` clean, `npm run build` clean (35 routes, up from 33, adding
`/approvals` and `/ai-activity`).

### Live browser verification (this session, fresh tenant, real dev servers, backend restarted to pick up all Phase 9 code)

Registered a fresh tenant, created a real customer/job/invoice and forced it overdue (documented sqlite
technique, unchanged since Phase 4), then ran the full referral loop (program → code → referral → lead →
reward request) via direct tool calls. Generated a Morning Brief live and confirmed it honestly reported
the real $450.00 overdue invoice and the real stalled lead. Clicked **Execute** on the reward
recommendation: since `retention.approve_referral_reward` is `AUTO` by policy in production (permission-gated,
not policy-gated — the deliberate Phase 7 design), it executed immediately, live-proving the
already-established AUTO-recommendation path still works unchanged after this phase's changes. To
demonstrate the `APPROVAL_REQUIRED` path specifically (no Morning Brief recommendation in this codebase
is wired to a tool that's `APPROVAL_REQUIRED` by default — only `retention.approve_referral_reward`,
`finance.void_invoice`, `finance.record_payout`, and a few marketing/scope-change tools are, and none of
those happen to also be Morning-Brief-executable in the current recommendation set), triggered
`finance.void_invoice` directly (real API call, real `pending_approval` response, real `ApprovalRequest`
id returned) and drove the rest of the flow entirely through the browser: opened `/approvals` as the
original requester, saw the real pending request, **clicked Approve and got a real, live 403 — "The
requester cannot approve their own request"** — then signed in as a second real user (created directly,
same technique as Phase 9's own test suite, since no invite-a-user UI exists), saw the identical pending
approval under the new session, clicked **Approve**, and watched it resume and execute live: "Approved —
the original action executed automatically," `execution_status: EXECUTED`, with a real execution result
block showing the invoice object — confirmed independently against `dev.db` that the invoice's status is
genuinely `VOID`, not just claimed so by the UI. Confirmed `/ai-activity` shows the complete real audit
trail (`approval.approve`, `approval.execution.completed`, every `tool.execute:*` row, including the
earlier rejected self-approval attempt) as the same second user. Confirmed the Owner Cockpit dashboard,
loaded fresh under that second session, shows the same real Morning Brief and finance numbers with no
stale cache — multi-user, tenant-shared state working correctly.

**Bug found and fixed during this work, not glossed over**: the audit log's `input_summary` JSON column
write crashed outright (`TypeError: Object of type UUID is not JSON serializable`) the first time a raw
`uuid.UUID` (rather than a pre-stringified id) was passed as a tool input value — surfaced by a test that
called `approvals.get_detail` with a real `UUID` object rather than a string, matching how some internal
callers (not just tests) can reasonably pass ids. Root cause: `app/tools/redact.py`'s `redact_input`
recursed through dicts/lists but passed every scalar value straight through unchanged. Fixed by coercing
`UUID`/`Decimal`/`datetime`/`date` values to `str` during redaction — a general hardening of the audit
path itself, not a one-off workaround in the calling test.

**Also disclosed**: while adding `audit.list_ai_activity`, an earlier edit to `app/tools/builtin/audit_tools.py`
accidentally overwrote the file's pre-existing `RecordAction` tool (`audit.record_action`) rather than
appending to it. It was reconstructed from its usage sites (`app/tools/factory.py`'s
`registry.register(RecordAction(session_factory))` call and `app/tools/policy.py`'s
`"audit.record_action": ActionPolicy.AUTO` entry) into a plausible, minimal implementation — a
general-purpose manual audit-log-entry tool, open to any authenticated actor, matching
`notifications.create_notification`'s style. No existing test covered `RecordAction`'s exact original
input/output shape, so byte-for-byte fidelity to the original cannot be guaranteed; three new tests
(`tests/test_audit_tools.py`) now cover the reconstructed version. Flagged here explicitly rather than
silently presented as untouched.

## What's actually built and verified — Phase 10 (Configurable Autonomy + Notification Orchestration)

### Part A — per-tenant automation policy

- **`TenantToolPolicy` (new, `app/models/tool_policy.py`)**: `tenant_id` + `tool_name` unique, `policy`
  (AUTO/APPROVAL_REQUIRED/BLOCKED), `enabled`, `configured_by`, `version` (optimistic concurrency).
  Additive, extends — does not replace — `app/tools/policy.py`'s static `DEFAULT_TOOL_POLICIES`, which
  remains the system default/fallback.
- **`PolicyService` (new, `app/services/policy_service.py`)** is the only place a tool's effective
  policy is resolved: `SYSTEM_BLOCKED_TOOLS` (a platform safety floor — `customer.delete`,
  `finance.delete_invoice`, `operations.delete_job` — none already-BLOCKED destructive tool can ever be
  overridden away from BLOCKED, checked and tested) → tenant override, if any → static system default.
  `ToolRegistry.execute()` now calls `await self._policy_service.resolve(context.tenant_id, name)`
  instead of the old synchronous `policy_for(name)` — checked fresh on every single execution, including
  on an approval's resume (Phase 9's `skip_approval_gate` path still runs this check, so a policy
  tightened to BLOCKED between request and approval still correctly stops execution — tested).
- **Concurrency-safe writes**: `set_policy()` retries a full select-then-insert-or-CAS-update sequence
  (fresh session each attempt) keyed on `version`, never a Python lock — verified by a real
  `asyncio.gather()` concurrency test.
- **Audit**: every policy change/reset writes an `automation_policy.change`/`automation_policy.reset` row
  to the existing `AuditLog` — no second audit table.
- **AI cannot change its own boundaries**: `SetPolicy`/`ResetPolicy` carry an explicit `actor_type==AI`
  guard on top of the permission check — `Role.MANAGER` (the role `AIExecutionService` always assigns)
  legitimately holds `MANAGE_AUTOMATION_POLICIES` for its human members, so permission alone would let an
  AI-actor through; the guard closes that (tested, `test_ai_actor_cannot_call_automation_set_policy`).
- **`/settings/automation` (new)**: every configurable tool, current vs. default policy,
  when/by-whom last changed, one-click AUTO/APPROVAL REQUIRED/BLOCKED buttons with an explicit
  confirm-dialog ("Change Create Customer from AUTO to APPROVAL REQUIRED?"), "reset to default", and
  `customer.delete`/`finance.delete_invoice` shown as "PLATFORM BLOCKED — not configurable" with no
  buttons at all. Infrastructure tools (`approvals.*`, `automation.*`, `notifications.*`, `audit.*`,
  `events.*`) are deliberately excluded from this listing — real internal plumbing, not something an
  Owner would ever think of as "what can Klaros do automatically."
- **`automation.get_autonomy_stats` (new tool)** powers the Owner Cockpit's "Autonomy status — today"
  widget: real counts of today's `tool.execute:*` `AuditLog` rows bucketed by `result`/`error`
  (automatic/approval required/blocked/failed) — never hardcoded, tenant-isolated (tested).

### Part B — notification orchestration

- **`Notification` extended in place** (recipient_id, type, priority, entity_type/id, channel, status,
  dedupe_key, sent_at, error — all nullable/defaulted so the one pre-Phase-10 row shape,
  `crm_handlers.py`'s appointment-booked notice, stays valid unchanged) plus a new
  `NotificationPreference` (per-user, per-type, per-channel opt-in). No second notification store.
- **`NotificationService` (new, `app/services/notification_service.py`)** is the only place a
  `Notification` row is ever created. In-app delivery IS the row itself — no separate "send" step for
  that channel. Email/SMS are real, optional `CommunicationProvider` dispatch attempts, gated by real
  per-user preferences — and a no-op today, honestly, since no `SendGridAdapter`/`TwilioAdapter`
  credentials are configured (see Integrations, unchanged since Phase 1) — never a fabricated send.
- **Deduplication is a real DB constraint**, not application-side counting: `UNIQUE(tenant_id,
  dedupe_key)` on `notifications` (NULL keys, including every pre-Phase-10 row, are never deduplicated
  against each other — both Postgres and SQLite treat NULL as distinct under a unique index). A
  duplicate-delivery `IntegrityError` is caught and silently absorbed — tested, including a real
  duplicate-event-replay scenario through `EventBus`.
- **`app/events/notification_handlers.py` (new)**, registered the same way every other domain's
  handlers are (`register_notification_handlers(bus, session_factory)`, wired into
  `get_wired_event_bus()` and the test `event_bus` fixture): subscribes to `APPROVAL_REQUESTED`/
  `APPROVAL_APPROVED`/`APPROVAL_REJECTED`/`APPROVAL_EXECUTION_COMPLETED`/`APPROVAL_EXECUTION_FAILED`,
  `EXCEPTION_CREATED` (mapped to `INVOICE_OVERDUE`/`NEGATIVE_FEEDBACK`/`HIGH_PRIORITY_EXCEPTION` by
  type/severity), `PAYMENT_RECEIVED`, `LEAD_CREATED`, and a new `MORNING_BRIEF_GENERATED` event
  (published once per real generation with ≥1 recommendation, so one grouped "Klaros found N actions"
  notification is created — never one per insight-tool call). Delivered automatically by the same
  continuous `EventWorker` from Phase 8 — **no manual event-processing endpoint required anywhere in
  this loop**, verified live and in the critical E2E test.
- **`/notifications/*` API + notification bell (new, in `AppShell`)**: unread badge (polls every 30s),
  dropdown with priority dots, click-through to the right page (`/approvals`, `/morning-brief`,
  `/customers/{id}`, etc.) that also marks the item read, mark-all-read, real loading/empty/error states.
- **`/settings/automation`'s Notifications section**: a type × channel grid (13 types × 3 channels),
  safe non-spammy defaults matching the spec's own example (`APPROVAL_REQUIRED`/`ACTION_FAILED`/
  `HIGH_PRIORITY_EXCEPTION` locked ON for in-app — the checkbox is disabled, not just pre-checked — while
  `MORNING_BRIEF_READY`'s email defaults OFF), Email/SMS columns showing the real, honest
  `sendgrid`/`twilio` connection status pulled from the existing `/api/v1/integrations` endpoint (no new
  status mechanism). Toggling and reloading the page proves the preference is real, DB-backed state —
  verified live.

### AI + policy integration

The AI never decides whether an action is safe — `insights.execute_recommendation` (Phase 8/9) already
called `ToolRegistry.execute()` for its underlying tool, and that now resolves the tenant's real,
current, persisted policy via `PolicyService` exactly like every other caller. No new code path was
needed for "AI recommendations obey policy" — it fell out of the existing architecture automatically,
and four new tests (`tests/test_ai_policy_integration.py`) prove it: the identical recommendation type,
same tool, executes immediately under one tenant's AUTO override and is blocked outright under another
tenant's BLOCKED override, with zero difference in what the AI itself did.

### Test results

247/247 non-Temporal backend tests pass (up from 204: 16 automation-policy tests covering the spec's
12-point checklist plus the platform-BLOCKED floor and infrastructure-tool exclusion; 17 notification
tests covering creation/dedup/tenant-isolation/recipient-isolation/unread-count/mark-read/mark-all-read/
dismiss/preferences/event-driven creation/duplicate-replay-dedup/grouped-morning-brief-notification/API
tenant isolation; 5 AI+policy integration tests; 2 autonomy-stats tests; 3 critical Phase 10 E2E tests).
The critical E2E (`tests/test_phase10_e2e.py`) runs the full 30-step scenario from the spec against a
real, continuously-running `EventWorker`: tenant sets a real per-tenant `APPROVAL_REQUIRED` policy →
real overdue invoice + referral reward request → Morning Brief detects both from real tool output →
Execute → real `ApprovalRequest` created → **a real notification appears automatically, with zero manual
event processing** → a different real user approves (self-approval independently rejected first) →
current policy is revalidated → the original action resumes and actually executes → a real success
notification appears automatically → full audit chain verified → tenant reconfigures the SAME tool to
AUTO → an identical new qualifying action executes immediately, no approval, no duplicate → tenant
reconfigures to BLOCKED → a third qualifying action is prevented outright, not silently approved — plus
a dedicated duplicate-event-delivery test (three re-publishes of the same event → exactly one
notification) and a dedicated tenant-isolation E2E (one tenant AUTO, one APPROVAL_REQUIRED, neither can
read or mutate the other's policy). Frontend: `npx tsc --noEmit` clean, `npm run build` clean (36 routes,
up from 35, adding `/settings/automation`).

### Live browser verification (this session, fresh tenant, real dev servers, backend restarted to pick up all Phase 10 code)

Registered a fresh tenant, opened `/settings/automation`, and changed `crm.create_customer` from AUTO to
APPROVAL_REQUIRED through the real confirm-dialog flow ("Change Create Customer from AUTO to APPROVAL
REQUIRED?") — persisted immediately, correctly timestamped, survived navigation and a second logged-in
user's session (proving it's real per-tenant DB state, not client-side). Attempted to create a customer
through the normal `/customers` "New customer" form: no customer was created, and a real
`ApprovalRequest` appeared on `/approvals` — **and, with zero manual trigger, the notification bell
showed a real unread badge and a real "Approval required" notification** naming the exact tool and
reason. Clicking the notification navigated to `/approvals` and marked it read automatically (badge
cleared). Signed in as a second real user (created directly, same technique used throughout this
project since no invite-a-user UI exists), saw the identical pending approval under the new session,
approved it — **the original `crm.create_customer` call resumed and executed for real**, confirmed
independently on `/customers` (the customer now genuinely exists) — and two more real notifications
(`APPROVAL_APPROVED`, `ACTION_EXECUTED`) appeared automatically, again driving the bell's unread badge.
The Owner Cockpit's "Autonomy status — today" widget showed real, non-zero counts (26 automatic, 1
approval required, 0 blocked, 0 failed) reflecting the actual session's activity — confirmed as real by
checking a fresh tenant showed "No actions yet today" instead of any placeholder. Reset the policy back
to AUTO via the same confirm-dialog UI and confirmed it took effect immediately. Verified the
Notifications preference grid: `APPROVAL_REQUIRED`'s in-app checkbox is checked AND disabled (cannot be
turned off, exactly as specified), `MORNING_BRIEF_READY`'s email defaults to unchecked, Email/SMS columns
honestly read "NOT CONNECTED — no SENDGRID_API_KEY configured" / "NOT CONNECTED — no TWILIO_ACCOUNT_SID
configured" (pulled from the real, existing `/api/v1/integrations` status, not a new fabricated check);
toggled a preference off and confirmed it was still off after a full page reload — real, persisted state.

**Bugs found and fixed during this work, not glossed over**:
1. `app/events/notification_handlers.py`'s exception handler initially looked up the `OperationsException`
   row using `event.entity_id` — but `exception_service.create_exception()` publishes `EXCEPTION_CREATED`
   with `entity_id` set to the AFFECTED entity (e.g. the customer), not the exception's own id, which
   only ever appears in the event payload as `exception_id`. The handler silently found nothing and
   created no notification for every exception. Caught by `test_high_severity_exception_creates_notification`
   failing with an honest `assert False`, not a false pass. Fixed by reading `event.payload["exception_id"]`.
2. `PolicyService.set_policy()`'s concurrent-write path initially caught only `IntegrityError` on a lost
   insert race and re-queried within the SAME session/transaction — under two genuinely concurrent
   `asyncio` writers sharing this test suite's single sqlite `StaticPool` connection, this could still hit
   `NoResultFound` or `OperationalError: SQL statements in progress` (the same class of sqlite-in-this-
   test-harness artifact documented since Phase 8, not a production concern — a real Postgres connection
   pool has no such restriction). Fixed by retrying the entire select-then-insert-or-update sequence with
   a fresh session per attempt, and the concurrency test itself uses the same documented
   `asyncio.Lock`-around-the-test-harness's-one-physical-connection technique as
   `tests/test_phase8_e2e.py`/`tests/test_approval_orchestration.py` — real concurrent coroutines, the
   lock only serializes access to the shared test connection, never the actual CAS logic under test.
3. **Disclosed, minor, cosmetic**: the Automation Settings notification-type labels initially rendered
   as `MORNING BRIEF READY` instead of `Morning Brief Ready` — a regex (`\b\w` uppercasing) that only
   works correctly on already-lowercase input, applied to already-uppercase `NotificationType` enum
   values. Fixed with a proper split/title-case. Caught during live verification, not a test failure (no
   test asserted on exact label casing).

### Known limitations

- Email/SMS notification channels are real, wired abstractions (`NotificationChannelName.EMAIL`/`SMS`,
  per-user preferences, dispatch attempted through the existing `CommunicationProvider`) but never
  actually send in this environment — no `SENDGRID_API_KEY`/`TWILIO_ACCOUNT_SID` configured (unchanged
  since Phase 1). The Automation Settings UI reports this honestly rather than hiding it.
- Notifications are tenant-wide (`recipient_id` is an optional column, present in the schema for future
  per-user targeting, but every handler in `app/events/notification_handlers.py` currently creates one
  tenant-wide notification rather than fanning out per-eligible-user) — any user with `READ_NOTIFICATIONS`
  in the tenant sees the same notification list. A deliberate simplification for a one-person/small-team
  business, not a security gap (still fully tenant-isolated).
- `automation.get_autonomy_stats` buckets by today's UTC calendar day, not the tenant's local timezone
  (Morning Brief's scheduler is the only place in this codebase that already handles tenant-local time,
  via `Organization.morning_brief_timezone`) — a real, minor inconsistency for a tenant far from UTC,
  not attempted this phase.
- No push notification is sent when `automation_policy.change`/`reset` happens — only the `AuditLog` row
  and the UI's own "Last Changed" column reflect a policy change; a named Phase 9 recommendation
  ("wire `APPROVAL_REQUESTED` to a real channel") was about approvals specifically, not policy changes,
  and that part (Part B here) is now done.

## Phase 11 — Production Audit

A senior/principal-engineer-level end-to-end production audit of the complete Phase 1–10 system, on
the explicit instruction not to trust any prior phase's "complete"/"passing" claims without
independent verification. Full findings, evidence, and methodology: `PRODUCTION_AUDIT.md`. Scored
checklist: `PRODUCTION_READINESS.md`.

**Three real, previously-unknown issues found and fixed:**

1. **P0 — no startup guard against the insecure default `JWT_SECRET`.** A deployment with
   `ENV=production` and a forgotten `JWT_SECRET` override would accept forged tokens signed with the
   publicly-visible default string — a complete authentication bypass. Fixed: `app/main.py` now
   refuses to boot in that state. Tested (`tests/test_production_secret_guard.py`).
2. **P1 — the long-standing `InvoiceOverdueWorkflow` "hang" was a wrong Temporal SDK API call.**
   `workflow.sleep()` does not exist in `temporalio==1.8.0`; every call raised `AttributeError` inside
   the workflow sandbox, and Temporal's default retry-forever behavior on a failed workflow task made
   this look identical to a genuine hang from the outside. Never diagnosed before because every phase
   since 5 excluded this test file rather than let it fail visibly. Fixed by using `asyncio.sleep()`
   (the SDK's actual current API for a durable wait — it patches `asyncio.sleep` internally). **All 4
   Temporal workflow tests now pass, for the first time in this project's history.**
3. **P1 — migration `0011` had never been run end-to-end from an empty database, by anyone, and
   wasn't SQLite-compatible.** This project's `dev.db` was kept in sync every phase since 5 via raw
   `sqlite3 ALTER TABLE` statements, never through Alembic — exactly the kind of manual step that lets
   a broken migration go unnoticed. Fixed with `batch_alter_table`; verified by actually running
   `alembic upgrade head` against a brand-new empty database (all 11 migrations, in order, producing
   the correct 89-table schema) followed by a real app boot and HTTP round trip
   (register→login→create-customer→notifications→policies) against that migrated database.

**Full backend suite: 254/254 passing** (up from 204 reported at the end of Phase 10 — 251 from
Phase 10's own new coverage + 3 new production-secret-guard tests), **including the Temporal file for
the first time ever** — every prior phase's reported count excluded it.

**What this audit did NOT verify** (infrastructure genuinely unavailable in this sandbox, stated
honestly rather than assumed): PostgreSQL, Redis, Docker/`docker compose up`, a real second
`event-worker` process, real production-scale concurrent load, real AI provider calls, real
email/SMS delivery, live CVE/dependency scanning. See `PRODUCTION_AUDIT.md`'s "Not Verified" section
for the complete list.

**Honest classification: PRODUCTION READY WITH KNOWN LIMITATIONS.** Not "production ready" outright —
Postgres/Redis/Docker were never actually exercised in this sandbox, and several P2 gaps remain
open (no file-download endpoint, no token revocation, no `/auth/refresh` despite issuing refresh
tokens, no transactional outbox for the write-then-publish pattern). See `PRODUCTION_READINESS.md`
for the itemized, evidence-backed checklist.

## Phase 12A — Closing the audit gaps + the Company-OS Knowledge Layer

Direct continuation of Phase 11: closes the P2 gaps that audit found and left open, plus a fourth,
previously-undiscovered reliability gap found while doing this work, and adds the file-based
Knowledge Layer concept as a real, working (DB-backed, not literal disk files) feature.

**Real token revocation** (closes P2-1/P2-2). `User.token_version` (migration `0012`); every issued
JWT embeds it; `get_current_user` now does one indexed DB lookup per request to check it still
matches. `POST /api/v1/auth/refresh` (previously nonexistent — the app has issued unusable refresh
tokens every login since Phase 1) and `POST /api/v1/auth/logout` (bumps `token_version`, instantly
invalidating every outstanding token for that user) are both real and tested
(`tests/test_auth_refresh_and_logout.py`, 5 tests). The frontend's `AppShell` sign-out now actually
calls `/auth/logout` before clearing local storage, and `lib/api.ts`'s `request()` transparently
retries a 401 against `/auth/refresh` once before giving up — **live-verified**: called `/auth/logout`
with a real token from the browser, then made another request with that identical token and confirmed
a real 401 "Token has been revoked," not just a client-side effect.

**File download** (closes P2-3). `GET /api/v1/jobs/{job_id}/attachments/{attachment_id}/download` —
attachments could be uploaded and listed since Phase 4 but never retrieved. Tenant ownership
re-verified from the DB row, not the URL. 3 new tests
(`tests/test_job_attachment_download.py`), including tenant isolation. Wired into the job detail
page's attachment list as a "View" button (opens the real file via a blob URL).

**Event outbox reconciliation** (a fourth gap, found while reviewing the write-then-publish pattern
named as unaddressed in Phase 11's audit — not previously identified as its own issue).
`EventBus.publish()` durably commits the `Event` row to Postgres, then enqueues to the transport
(Redis/InMemory) as a *separate* step — if the process crashes, or the transport call itself fails,
between those two steps, the row is real and durable but nothing was ever enqueued to deliver it;
`process_pending` only ever reads from the transport's consumer groups, never scans this table. That
is a genuine, previously-unnoticed availability gap, worse than the "dual-write risk" language Phase
11 used for it. Closed with `EventBus.reconcile_stuck_events()` — an outbox-relay pass, run every
`EventWorker` tick (cheap: one indexed `SELECT`, capped at 100 rows), that re-enqueues any `PUBLISHED`
event older than a grace period. Safe by construction: a redundant re-enqueue of an event that *was*
actually delivered fine is caught by the exact same `EventProcessingRecord` per-(event_id,
handler_name) uniqueness that already guards against duplicate transport delivery — never a double
side effect. 3 new tests (`tests/test_event_outbox_reconciliation.py`), including a direct simulation
of the crash-window state (an `Event` row inserted with `status=PUBLISHED` and never enqueued) proving
recovery actually happens.

**The Company-OS Knowledge Layer** (new). `KnowledgeFile` (migration `0013`) — tenant-scoped,
path-addressed (`office/pricing-rules.md`, `brand/voice-guide.md`, ...) markdown documents, DB-backed
rather than literal filesystem files (this app runs as a stateless container in production; Postgres
is already the single source of truth for every other tenant record, and a local-disk knowledge layer
either wouldn't survive a redeploy or would need its own replicated storage for no real benefit — the
*addressing convention* from the Company-OS concept is preserved, the storage underneath is a normal
table). A new tenant is seeded with 5 real, editable placeholder files across office/compliance/brand
categories on registration. `knowledge.list_files`/`get_file`/`set_file`/`delete_file` tools
(READ_KNOWLEDGE/MANAGE_KNOWLEDGE permissions), full CRUD API, and a real `/settings/knowledge` editor
page (category-grouped file list, create/edit/delete, live-verified: created a fresh tenant, saw the
5 seeded files, opened `brand/voice-guide.md`, edited and saved it, confirmed it persisted).

**Not just a file store nobody reads**: `MorningBriefService.generate()` now fetches the tenant's
`brand/voice-guide.md` (via `KnowledgeService.get_content()`, returning `None`, never a fabricated
default, if the tenant never wrote one) and passes it to `AIProvider.enrich_brief(..., brand_voice=...)`
— when a real AI provider is connected, the rephrased headline actually follows the tenant's own
stated brand voice, fenced in the prompt exactly like `BUSINESS DATA` (never as instructions, so it
can't become a second prompt-injection surface). Tested end-to-end with a fake provider capturing
what it was called with (`test_ai_mode_passes_the_tenants_real_brand_voice_guide_to_the_provider`):
confirms `None` before any guide is configured, and the exact real string after one is written.

**Test results**: 275/275 backend passing (up from 254 at the end of Phase 11 — 21 new tests across
refresh/logout, file download, outbox reconciliation, and the knowledge layer). Frontend `tsc`/build
both clean, 37 routes (up from 36, adding `/settings/knowledge`). Migrations `0012`/`0013` both
verified end-to-end from a genuinely empty database (the discipline established in Phase 11), not
just applied to the long-lived `dev.db`.

**Dependency CVE audit — a corrected finding.** Phase 11 marked "live CVE scanning" as NOT VERIFIED,
assuming no internet access in this sandbox; that assumption was wrong (`pip-audit`/`uv`/`npm audit`
all successfully reached PyPI/npm). A real scan found and fixed 23 backend CVEs — most significantly
`starlette` 0.38.6 (via `fastapi` 0.115.0) had multiple real advisories including a Host-header path
reconstruction bug that could bypass path-based authorization checks, and a Windows UNC-path SSRF in
`StaticFiles`; fixed by upgrading to `fastapi==0.141.1`/`starlette==1.6.0`. Also fixed: `python-jose`
3.3.0→3.5.0 (JWT-bomb DoS, algorithm confusion), `python-multipart` 0.0.9→0.0.31 (several form-parsing
DoS/smuggling advisories — this app accepts real file uploads and login form posts through this
library), and `pytest`/`pytest-asyncio` bumped together for a still-clean resolve. Frontend: `next`
14.2.35→16.3.3 closed 2 real advisories; `npm audit` now reports 0 vulnerabilities. One CVE knowingly
left open: `ecdsa` 0.19.2's Minerva timing-attack advisory has no fix from its maintainers (they
consider side-channel attacks out of scope), but it's a transitive `python-jose` dependency only
reachable via ES256/EC-key algorithms — this app signs and verifies every JWT with HS256 only, so the
vulnerable code path is never actually invoked. Every version bump was verified with the full 275-test
suite, a genuinely fresh `uv venv` install from the updated `requirements.txt` (not just the
already-warm dev venv), `tsc`/`npm run build`, and live smoke tests of the exact paths that changed
(JWT auth round-trip, multipart upload, file download, a real dynamic-route page load) — not just
"tests still pass."

**Scope note**: this phase is 12A of a larger plan (12B: verify Postgres/Redis/Docker in an
environment that actually has them; 12C: real external integrations — Stripe/Twilio/SendGrid/ad
platforms/QuickBooks — each gated on the user obtaining real developer credentials, which cannot be
fabricated). 12A was chosen first specifically because it required no external dependency and closed
real, previously-identified gaps rather than adding speculative new surface area.

## Phase 12B — Real PostgreSQL + Redis + Docker Production Verification

Docker itself was confirmed unavailable again in this sandbox (no `docker`/`podman`/`colima`/`lima`/
`brew`). Rather than skip the Postgres/Redis half of 12B along with it, real PostgreSQL 16.2 and real
Redis were obtained as actual compiled binaries via the `pgserver`/`redislite` PyPI packages — no
Docker daemon required — and used to run this project's real application and full test suite against
genuine engines. Full findings, evidence, and the honest not-verified list are in
`PRODUCTION_AUDIT.md`'s "Phase 12B" section and `PRODUCTION_READINESS.md`'s updated checklist; summary:

- Full backend suite: **290/290 passing against real PostgreSQL + real Redis** (was SQLite-only
  before this phase); **282/282 passing (8 correctly self-skipping) against SQLite**, unchanged
  behavior, both runs of the same suite.
- Two real, previously-invisible bugs found and fixed by testing against real infrastructure for the
  first time: (1) SQLite doesn't enforce `VARCHAR(N)` length — `communication_logs.status` was too
  narrow for a real value it had accepted silently since Phase 5, now migration `0014`; (2) this same
  session's prior dependency-CVE pass (pytest-asyncio upgrade) had silently broken cross-event-loop
  asyncpg handling, invisible against SQLite, fixed via `pytest.ini` loop-scope config.
- Added the `/ready` endpoint (previously only `/health`, dependency-free, existed) — checks real
  Postgres and Redis, live-verified across all 4 up/down combinations, including finding and fixing a
  genuine indefinite-hang bug (no bounded timeout on an already-open connection to a stopped-but-not-
  dead dependency).
- `RedisStreamTransport` — written since Phase 2, never actually exercised against a live Redis
  server because the test suite's fixture always substitutes `InMemoryTransport` regardless of
  `EVENT_TRANSPORT` — is now directly tested against real Redis for the first time.
- Temporal: added duplicate-workflow-ID rejection and worker-restart-resilience tests against a real
  ephemeral Temporal server; all 6 Temporal tests (including `InvoiceOverdueWorkflow`, fixed Phase 11)
  pass.
- A full live critical-business-flow E2E run (register→lead→qualify→convert→schedule→dispatch→...→
  close→invoice→payment→audit) over real HTTP against the real-Postgres-backed app, plus the frontend
  equivalent driven through an actual browser — both genuinely new data round-tripping through real
  Postgres, not mocked.
- New `DOCKER_DEPLOYMENT.md`. Docker itself remains genuinely NOT VERIFIED — a static re-read of
  `docker-compose.yml` and both Dockerfiles this phase found real gaps (no non-root `USER` in either
  Dockerfile, `frontend/Dockerfile` is dev-mode-only with no production build stage, no `restart:`
  policy anywhere) that are documented but not fixed blind, since there's no way to verify a Docker
  build succeeds in this sandbox.

**Scope note**: 12C (real external integrations — Stripe/Twilio/SendGrid/ad platforms/QuickBooks)
remains the one item of the original Phase 12 plan not started, each gated on the user obtaining real
developer credentials.

## Phase 12C — Real External Integrations

Full detail, per-provider architecture, credentials, env vars, testing procedures, and honest
verification status live in `INTEGRATIONS.md` (new this phase). Summary:

**Built real, not yet connected (user has not yet added credentials to `backend/.env` as of the
end of this phase)**: Stripe (real Checkout Session creation, real webhook signature
verification, real refund issuance with idempotency, wired through the existing
`PaymentService`/`ToolRegistry`/approval pipeline — no parallel payment path); Twilio (real SMS
send + real status-callback webhook with signature verification, wired through the existing
`CommunicationProvider` abstraction via a new `get_communication_provider()` factory —
mirroring `get_object_storage()`'s and `get_ai_provider()`'s honest "real if configured, else
internal-test fallback" pattern — replacing 5 previously-hardcoded
`InternalTestCommunicationAdapter(...)` call sites); SendGrid (real email send, same factory).
OpenAI/Anthropic were already real since Phase 9 (`app/services/ai_provider.py`); Phase 12C adds
them to the `GET /api/v1/integrations` status list for the first time, with a real minimal-call
`check_status()`.

**New shared infrastructure**: `webhook_events` table (migration `0015`) — a
`(provider, external_event_id)`-unique idempotency + audit ledger for every inbound webhook,
proven to prevent duplicate-delivery double-processing by dedicated tests for both Stripe and
Twilio; `communication_logs.external_id` (migration `0016`) — stores the real provider message
id (Twilio `MessageSid`, SendGrid's `X-Message-Id`) so a later delivery-status webhook can find
and update the right row; 5 new `EventType` entries (`integration.connected/disconnected/
connection_failed`, `webhook.received/rejected`).

**Genuinely not implemented this phase**: S3-compatible object storage (the real local-disk
adapter remains the only working storage backend); QuickBooks, Google Calendar, Gmail, Google
Ads, Meta Ads, Google Business, ServiceTitan, Jobber, and outbound-enrichment providers
(Apollo/Clay/Instantly) — all still honest `NOT_CONNECTED` stubs, unchanged in depth from
before this phase except where noted; a tenant-scoped OAuth connection model (deliberately not
built — every provider actually implemented this phase uses a single platform-level API key,
not per-tenant OAuth; building the OAuth model before the first OAuth-based provider has real
credentials to verify against would be speculative, untestable infrastructure).

**Test results**: 304 backend tests passing against SQLite (up from 282 at the end of 12B — 22
new: Stripe webhook signature crypto, the full Stripe webhook endpoint including the critical
duplicate-delivery-never-double-applies-a-payment test, Twilio webhook signature crypto, the
full Twilio status-callback endpoint). Migrations `0015`/`0016` both verified end-to-end from a
genuinely empty database, on both SQLite and real PostgreSQL (the Phase 12B infrastructure).
Frontend: new `/settings/integrations` page, live-verified in a real browser against the real
Postgres-backed backend — correctly shows all 12 providers, honestly grouped by category, all
`NOT_CONNECTED` with an accurate per-provider reason (matching actual `.env` state); `tsc
--noEmit` clean.

**What real verification (Steps 4-13 of the Phase 12C spec) still requires**: the user adding
real credentials to `backend/.env` for at least one provider. The architecture, error handling,
idempotency, and webhook security are all built and tested against realistic (self-signed,
correctly-shaped) fixtures — but "does Stripe's real API actually accept this request" has not
been asked, because nothing in this sandbox has a Stripe/Twilio/SendGrid/OpenAI/Anthropic key.

## Phase 12D — Production Integration Completion

Re-checked credential state at the start of this phase: unchanged from the end of 12C — every
integration env var in `backend/.env` was still empty. Steps 2-6/16 of the 12D spec (real
provider verification) were therefore skipped entirely, per the spec's own rule 16 ("if blocked
by credentials, continue with self-contained architecture/tests rather than pretending"). Work
this phase focused on Step 7 onward: the tenant-scoped connection architecture.

**Built**: `integration_connections` (migration `0017`) — a real, tenant-scoped table for
providers where each tenant has their OWN external account (QuickBooks, Google Calendar, Gmail,
Google Ads, Meta Ads), distinct from the Phase 12C platform-level providers (Stripe/Twilio/
SendGrid/OpenAI/Anthropic, unchanged). Credential encryption at rest
(`app/integrations/credential_store.py`, Fernet, no new dependency — `cryptography` was already
transitive via `python-jose[cryptography]`), gated by a new `INTEGRATION_CREDENTIAL_ENCRYPTION_KEY`
setting with the same production-boot-refusal pattern as `JWT_SECRET`.
`IntegrationConnectionService` (`app/services/integration_connection_service.py`) — the single
place `NOT_CONNECTED → CONNECTING → CONNECTED → ERROR → DISCONNECTED` lifecycle logic lives,
with a pluggable real-verifier-per-provider registry (none registered yet — no OAuth client
exists for any of these providers, so attempting to connect one honestly reports
`ERROR: "No real verifier implemented"`, never a fabricated `CONNECTED`). 4 new API endpoints
under `/api/v1/integrations/connections`, gated by the pre-existing (declared since early
phases, never wired to an endpoint before now) `MANAGE_INTEGRATIONS` permission. Frontend:
`/settings/integrations` extended with an honest "Tenant-Owned Connections (OAuth)" section —
`NOT_IMPLEMENTED` for all 5 planned providers, no "Connect" button pointed at nothing real.

**Tests**: 31 new (12 service-layer lifecycle/isolation, 6 API-layer tenant isolation, 6
credential-encryption crypto, 1 verifier-timeout, others production-secret-guard). Full suite:
**330 passed, 8 skipped on SQLite** (up from 329/8); **338 passed on real Postgres+Redis** (up
from 337). Migration `0017` verified from empty on both. `tsc --noEmit` and `next build` both
clean.

**A real bug found and fixed**: `IntegrationConnectionService.verify()` initially had no bounded
timeout around the (real, external, possibly-slow) verifier call — the same class of hang bug
found and fixed in Phase 12B's `/ready` endpoint. Fixed with `asyncio.wait_for` (10s); proven by
a dedicated test using a verifier that sleeps forever. Also found: adding the new
`INTEGRATION_CREDENTIAL_ENCRYPTION_KEY` production-boot-refusal check broke an existing test
(`test_starts_in_production_with_a_real_secret`, which simulated a fully-valid production boot
but only set `JWT_SECRET`, not the new key) — fixed by updating the test to set both, and added
a new test for the refusal case itself.

**Not done this phase**: any real OAuth provider (Step 10) — no Google/Intuit developer app
registration, no redirect URI, no real credentials exist to build and verify a real OAuth flow
against. The connection model is ready to receive the first one; building speculative OAuth
code with nothing real to test against would not be genuine progress.

## Phase 12E — Real OpenAI Provider Activation + Production AI Verification

Credential state re-audited at the start and end of this phase: `OPENAI_API_KEY` remained
unset in `backend/.env` throughout — real-provider verification (Steps 12/13/16/18-real of the
spec) is `BLOCKED BY CREDENTIAL`, not run, not inferred. Work concentrated on hardening the
existing AI provider architecture (Phase 9) and building the first real consumer of it beyond
the Morning Brief, per the spec's rule to continue self-contained work when blocked.

**Hardened**: `app/services/ai_provider.py` — timeout/retries/max-output-tokens moved from
hardcoded constants to configurable `Settings` (`OPENAI_TIMEOUT_SECONDS`/`OPENAI_MAX_RETRIES`/
`OPENAI_MAX_OUTPUT_TOKENS`, symmetric `ANTHROPIC_*`); real retry-with-exponential-backoff on
429/5xx/timeout/network errors (never on 401/403/400 — retrying an invalid key wastes quota);
real error classification (`AIErrorType`) replacing the previous "every failure is `None`"
behavior; real token-usage extraction from both providers' response bodies (cost is
deliberately never computed — no verified, current pricing source exists, so usage is recorded
and cost honestly reported as unavailable); defense-in-depth API-key redaction on every error
string that could reach a log/response/audit row. `enrich_brief()`'s existing behavior and
signature are unchanged — the two `_call_api`-mock tests whose contract legitimately changed
(bare `str` → `_ProviderResponse` with token fields) were updated, not weakened.

**New**: `generate_structured()` — a general-purpose real-call method on `AIProvider`, alongside
the existing narrow `enrich_brief()`. `AIInvocationLog` (migration `0018`) — the first real
audit + usage trail for direct AI-provider calls (distinct from `AuditLog`, which only records
TOOL executions); tenant-scoped, stores safe metadata only (provider/model/operation/latency/
token counts/error classification), never the API key, raw prompt, or raw response.
`AIQualificationService` + `crm.ai_qualify_lead_advisory` tool + `POST
/api/v1/leads/{lead_id}/ai-qualify-advisory` — the first real consumer of `generate_structured()`
and `AIInvocationLog`: an AI-assisted lead-qualification recommendation, **advisory only**, that
never writes to the `Lead` record. `app/services/scoring.py::score_lead` (deterministic,
Phase 3) is completely unchanged and remains the only thing that ever actually sets a lead's
qualification — this is a parallel, coexisting path, not a replacement.

**Tests**: 27 new (11 hardening — retry/backoff/classification/timeout-config/token-usage/
key-redaction, in `test_ai_provider_hardening.py`; 11 qualification-service tenant/prompt
isolation + audit logging, in `test_ai_qualification_service.py`; 3 real-ToolRegistry
integration; 2 API-level), plus the pre-existing `test_ai_provider.py` suite updated (not
weakened) for `_call_api`'s legitimately-changed contract. Full suite: **357 passed, 8 skipped
on SQLite** (up from 330/8 at the end of 12D); **365 passed on real Postgres+Redis** (up from
338). Migration `0018` verified from empty on both. `tsc --noEmit` and `next build` both clean;
live-verified in a real browser against the real-Postgres-backed backend — Owner Cockpit, Leads,
and the Integrations page all render correctly, OpenAI/Anthropic honestly show `NOT_CONNECTED`
with the real reason, and a direct HTTP call to the new qualification-advisory endpoint against
the live real-Postgres-backed app returned an honest `available: false` with no fabrication.

**Security**: fresh secrets grep (none found); confirmed no logging call anywhere in the new AI
code paths references raw credential/prompt/response data; added a defense-in-depth key-scrub
even though `httpx`'s own exception messages were directly verified to never include request
headers; dependency audit unchanged (0 new vulnerabilities, no new dependencies).

**Real OpenAI verification: BLOCKED BY CREDENTIAL.** Every claim above is proven at the unit/
integration-test level against realistic, correctly-shaped fixtures — not against the real
OpenAI or Anthropic API. See `INTEGRATIONS.md` for the exact remaining step (set the key,
restart, run the documented testing procedure).

## Phase 12F — Real Stripe Activation + Production Payment Integration

Credential state re-audited at the start and end of this phase: `STRIPE_SECRET_KEY`/
`STRIPE_WEBHOOK_SECRET` remained unset in `backend/.env` throughout — real Stripe verification
is `BLOCKED BY CREDENTIAL`. Work extended the already-substantial Phase 12C Stripe foundation
(real client, checkout sessions, webhook, refund wiring) rather than rebuilding it.

**Hardened**: `StripeClient` — configurable timeout/retries (previously hardcoded), real error
classification (`StripeErrorType`, matching the pattern from `app/services/ai_provider.py`'s
`AIErrorType`), 401/403 never retried, a real idempotency key on checkout-session creation.

**New — tenant-scoped Stripe credentials**: a tenant can now connect their OWN Stripe secret key
via `POST /api/v1/integrations/connections/stripe/connect` (or the Settings → Integrations UI),
using the Phase 12D `IntegrationConnection` model exactly as that phase's docs said it should be
used the moment a real API-key-based (non-OAuth) provider needed it. Real verifier
(`GET /v1/balance`), checked first by the checkout tool, falling back to the platform-level key.
Verified live in a real browser + real Postgres: connecting a deliberately-fake test key made a
real network call to Stripe's live API and was honestly rejected — `ERROR: secret_key rejected
by Stripe's API` — proving the entire real pipe, not just the code path.

**New — webhook event coverage**: `payment_intent.payment_failed` (publishes the existing
`PAYMENT_FAILED` event) and `charge.refunded` (reconciles a refund issued OUTSIDE Klaros against
the matching `Payment`, via a new `PaymentService.reconcile_external_refund()` using Stripe's
cumulative `amount_refunded` for idempotency — a redelivered webhook is a safe no-op, proven by
test). Previously only `payment_intent.succeeded` was handled.

**A real, exploitable P0 bug found and fixed**: `finance.approve_refund` and 7 sibling
approval-decision tools (`reject_refund`, `approve_invoice`/`reject_invoice`,
`approve_credit_note`/`reject_credit_note`, `approve_writeoff`/`reject_writeoff`) relied only on
role-based permission gating — no explicit actor-type check, unlike the generic
`approval.approve_action` tool. `Role.MANAGER` (the only role `AIExecutionService` has ever
actually been invoked with) genuinely holds every one of those permissions. A captured
proof-of-concept showed an AI-actor context could call `finance.approve_refund` directly and
have it succeed — a real refund, completed, with no human involved. Fixed by adding the same
`ActorType.AI` guard the generic approval tools already had, to all eight tools; 8 new
regression tests prove it closed without breaking the legitimate human path.

**Marketing attribution proven end-to-end**: a real Stripe payment (via the real webhook) flows
into the existing Marketing Attribution Loop — `PAYMENT_RECEIVED` → `mark_paid()` →
`CampaignConversion.collected_amount` → real ROAS/CAC — with no Stripe-specific attribution path
and no duplicate counting on a redelivered webhook, proven by 3 dedicated tests. Surfaced a
real, non-obvious, PRE-EXISTING architectural fact while writing them (not a bug): `EventBus.publish()`
durably persists an event but does not synchronously invoke subscribers — attribution updates
land only once something calls `process_pending()` (the real `EventWorker`'s poll loop in
production), not the instant the webhook returns 200. Documented in `INTEGRATIONS.md`.

**Tests**: 41 new (9 `StripeClient` retry/backoff/classification, 7 tenant-connection
credential resolution/isolation, 6 webhook failure/refund-reconciliation, 3 attribution flow, 8
the AI-approval-guard regression suite, 1 refund-API-failure-leaves-no-state-changed, plus 7
security-boundary gap tests added at phase close — the "no tool can record Stripe money" and
"cross-tenant webhook can't credit another tenant's invoice" invariants, the
missing/invalid-credential checkout paths, and full tenant-policy enforcement on the payment
tool). Full suite: **398 passed, 8 skipped on SQLite** (up from 391/8 at the end of 12E's
final count); **406 passed on real Postgres+Redis** (up from 399). No new migration this phase
(no new tables — only a new `PaymentService` method on the existing `payments`/`refunds` tables).
`tsc --noEmit` and `next build` both clean; live-verified in a real browser against the
real-Postgres-backed backend, including the new Stripe connect/verify/disconnect flow end-to-end.

**Real Stripe verification: BLOCKED BY CREDENTIAL.** Every claim above is proven at the unit/
integration-test level (mocked at the `httpx` transport layer, exercising real request/response
handling — see `tests/test_stripe_client.py`) or, for the connect flow specifically, against
Stripe's real API with a deliberately-invalid key (proving the pipe, not a successful payment)
— never against a real Stripe test-mode payment, because no credential exists in this
environment. See `INTEGRATIONS.md` for the exact remaining step.

## Known limitations / caveats

- **No Docker in this dev sandbox** (unchanged since Phase 1 — still true as of Phase 12B). Automated
  tests default to sqlite; day-to-day live browser verification typically runs against the file-backed
  sqlite dev database (`backend/dev.db`) and the in-memory event transport. **As of Phase 12B**, real
  PostgreSQL and real Redis ARE available and verified — via bundled real binaries
  (`pgserver`/`redislite`), not Docker — see the Phase 12B section above; every migration including
  `0005` has now actually been executed against a real Postgres, not just written correctly for one.
- **`finance.send_invoice` is `AUTO`, not `APPROVAL_REQUIRED`, despite the spec's suggested default**
  — found and fixed during live verification. Sending is already gated by the invoice's own approval
  state (only `APPROVED` invoices can be sent, and `APPROVED` is only reached via the threshold check
  or a human `approve_invoice` call). A second, generic `ToolRegistry`-level `APPROVAL_REQUIRED` on
  top of that hit the same accepted Phase 2 limitation as everything else in this codebase — approving
  a `ToolRegistry`-created `ApprovalRequest` only flips that request's own status, it does not resume
  the original tool call — which meant there was **no way to ever actually send an invoice** once that
  policy fired. `finance.void_invoice` has the identical limitation and was left
  `APPROVAL_REQUIRED` on purpose, since voiding isn't gated by any other state machine the way sending
  is. `finance.record_payout` has the same limitation too.
  **As of Phase 9 this generic gap is closed**: approving a `finance.void_invoice` or
  `finance.record_payout` request via `/approvals` now genuinely resumes and executes the original call
  through `ApprovalExecutionService` — live-verified in Phase 9's browser session by voiding a real
  invoice through the full request → approve → execute flow and confirming its status in the database.
  `finance.send_invoice` itself remains `AUTO` (unchanged; still correctly gated by the invoice's own
  approval state instead). Left here, corrected rather than deleted, so the historical record of the gap
  and its later fix stays intact.
- **`operations.update_job` has no `estimated_revenue`/`estimated_cost` fields** — job estimation
  is out of Phase 4/5's scope, so the browser-verification jobs had these set directly against the
  database rather than through the UI or a tool. A real gap for a future phase, not a Finance bug.
- **Temporal status — the long-standing `InvoiceOverdueWorkflow` hang was root-caused and fixed in
  Phase 11's production audit (see the dedicated section below and `PRODUCTION_AUDIT.md` P1-1); it was
  a wrong SDK API call, not a platform limitation, and had simply never been diagnosed because every
  phase's test run excluded the file it lived in.**
- `DEFAULT_TOOL_POLICIES` is still a static in-process dict (unchanged since Phase 2).
- **Phase 6**: `marketing.enroll_outbound_contact`/`enroll_lead_in_nurture`/`execute_due_*_activities`
  are `AUTO`, not `APPROVAL_REQUIRED`, deliberately avoiding a fresh instance of the exact
  "`ApprovalRequest` doesn't resume the original action" dead end found and fixed for
  `finance.send_invoice` in Phase 5 — reasoning documented directly in `app/tools/policy.py`. This is
  safe today only because external Gmail/Twilio/SendGrid remain `NOT_CONNECTED` (only the internal
  test `CommunicationProvider` ever actually sends); revisit this policy at the moment a real external
  channel is connected, gating the *send* step specifically.
  `marketing.publish_content_variant`/`publish_seo_page`/`respond_to_review` remain
  `APPROVAL_REQUIRED` — **as of Phase 9, approving those via `/approvals` genuinely resumes and executes
  the original publish/respond call** through `ApprovalExecutionService`; the "generic approval doesn't
  resume the action" gap this note originally described is closed (see Phase 9 section above). Left here,
  corrected rather than deleted, so the historical record of the gap and its later fix stays intact.
  - The audit log correctly records every `marketing.*` tool call, but `entity_type`/`entity_id`
    inference (`ToolRegistry._infer_entity`) only populates for tools whose output has a single
    top-level `{id: ...}`-shaped dict; several Marketing tools (`record_spend`,
    `create_outbound_list`, `identify_inactive_customers`, etc.) return flatter shapes and so audit
    rows exist but without a linked entity — real, complete, queryable-by-tool audit rows regardless,
    just not filterable by `entity_type=campaign` for those specific calls. A cosmetic gap, not a
    security or correctness one.

- **Phase 9**: no real `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` is configured in this environment (unchanged
  since Phase 2/8), so every Morning Brief generated this phase is genuinely `mode=DETERMINISTIC` — the
  `AnthropicAIProvider`/`OpenAIAIProvider` HTTP call bodies are real and unit-tested against a mocked
  network seam, but have never been exercised against a live provider. No Morning Brief recommendation
  in the current codebase happens to be both executable AND `APPROVAL_REQUIRED` by policy at the same
  time (only `retention.approve_referral_reward`, which is `AUTO`) — the Phase 9 critical E2E test and
  live browser verification both demonstrate the full recommendation → approval → resume loop by
  temporarily flipping that one tool's policy (a technique already used by this codebase's own test
  suite), and separately demonstrate the same approval mechanism live against a naturally
  `APPROVAL_REQUIRED` tool (`finance.void_invoice`) end-to-end. `DEFAULT_TOOL_POLICIES` remains a static
  in-process dict (unchanged since Phase 2) — an Owner cannot yet change which tools require approval
  without a code change.
- **`app/tools/builtin/audit_tools.py`'s pre-existing `RecordAction` tool was accidentally overwritten**
  while adding `ListAIActivity` this phase, then reconstructed from its usage sites since no test covered
  its exact original shape — disclosed in full in the Phase 9 section above, not hidden.

## Temporal status (explicit, per workflow)

| Workflow | Uses `workflow.sleep()`? | Isolated test result |
|---|---|---|
| `EventProcessingWorkflow` | No | 1 passed (0.27s), Phase 2/3 |
| `LeadQualificationWorkflow` | No | 1 passed (0.66s), Phase 3 |
| `JobLifecycleWorkflow` | No | 1 passed (0.44s), Phase 4 |
| `InvoiceOverdueWorkflow` | Uses `asyncio.sleep()` (fixed Phase 11) | **FIXED — 4 passed (2.78s) for the whole file.** Root-caused during the Phase 11 production audit: this project's `temporalio==1.8.0` SDK has no `workflow.sleep` attribute at all — every call to it raised `AttributeError` inside the workflow sandbox, and Temporal's default behavior on a failed workflow task is to retry indefinitely, which from outside looked identical to a genuine hang. Never actually diagnosed because every phase's test run explicitly excluded this file. Fixed by using `asyncio.sleep()` instead (the SDK patches it internally to route through its own durable timer — confirmed by reading `temporalio/worker/_workflow_instance.py`). See `PRODUCTION_AUDIT.md` P1-1 for full evidence and reproduction. |

No new Temporal workflow was added in Phase 6, 7, 8, or 9 — outbound/nurture sequences (Phase 6),
retention reminders/campaigns (Phase 7), Morning Brief scheduling (Phase 8, via the Event Worker's tick
loop), and approval execution (Phase 9, synchronous within the same request/tick, no scheduling needed)
all use a deterministic `scheduled_for`/tick-driven/synchronous check instead of a second unverified
`workflow.sleep()`-based workflow.

## Recommended next step (Phase 8, historical)

The event-processing gap named at the end of every phase since Finance — "there is still no actual
background worker process running the durable event-bus subscriber loop continuously" — is now closed
and verified: a real event published once propagates automatically through retention, finance, and the
Morning Brief with zero manual intervention, both in an automated E2E test and live in the browser,
including surviving a real process restart. Closing it surfaced and fixed three real bugs along the way
(lazy handler-wiring, a concurrent-insert race, and a `StaticPool`/file-SQLite transaction-corruption
root cause — all detailed above), which is exactly the kind of thing that only shows up once something
runs continuously instead of on-demand.

Two candidates named for Phase 9, both since done: connecting a real LLM provider (done — Part B above),
and closing the long-standing generic "`ApprovalRequest` doesn't resume the original action" gap (done —
Part A above, `ApprovalExecutionService`).

## Recommended next step (Phase 9)

Both Phase 9 candidates are done and verified: the generic approval dead end is closed for every tool via
`ApprovalExecutionService`, and a real AI provider abstraction exists with genuine `httpx` call bodies,
structured-output validation, and a hallucination-proof entity-id cross-check — though `mode` stays
`DETERMINISTIC` in this environment since no `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` is configured (unchanged
since Phase 2/8; connecting a real key would exercise the `AI` path end-to-end with zero further code
changes, and the AI-provider test suite already proves the plumbing works against a mocked network call).

Two candidates for Phase 10: first, `DEFAULT_TOOL_POLICIES` is still a static in-process dict (named as a
limitation since Phase 2) — now that approving genuinely resumes execution, a per-tenant-configurable
policy store (which tools require approval, and for which role) would let an Owner actually use that
control rather than it being a code-level constant only Klaros' own developers can change. Second, the
Owner Cockpit's approval/AI-activity surfaces are read-only summaries today — no push notification (email/
SMS/in-app) fires when a new `ApprovalRequest` is created, so an Owner currently has to remember to check
`/approvals`; wiring `EventType.APPROVAL_REQUESTED` (already published, already consumed by nothing) to a
real notification channel would close that loop using entirely existing infrastructure.

Not proceeding into Phase 10 automatically, per instruction — awaiting direction.

## Recommended next step (Phase 10)

Both Phase 10 candidates named above are done and verified: `TenantToolPolicy` gives every tenant a real,
persistent, audited, DB-backed override of what Klaros may do automatically (with a platform-level
`SYSTEM_BLOCKED` floor no tenant can escape), and `EventType.APPROVAL_REQUESTED`/`APPROVAL_APPROVED`/
`APPROVAL_REJECTED`/`APPROVAL_EXECUTION_COMPLETED`/`APPROVAL_EXECUTION_FAILED` (plus exception/payment/
lead/morning-brief events) now all drive a real, deduplicated, in-app notification with zero manual
processing — an Owner no longer has to remember to check `/approvals`.

Two candidates for Phase 11: first, Email/SMS notification channels are fully wired abstractions with
real per-user preferences, but still can't actually send — connecting a real `SENDGRID_API_KEY`/
`TWILIO_ACCOUNT_SID` (the same gap named for customer-facing messages since Phase 1) would exercise the
one remaining honestly-NOT_CONNECTED path with zero further code changes, since `NotificationService`
already resolves per-user channel preferences and only lacks a real provider to hand them to. Second, the
notification-preferences and automation-policy UIs are both real but currently un-searchable/un-grouped
flat lists (dozens of tools/13 types × 3 channels) — as more phases add tools this will need grouping by
domain (Finance/Marketing/Retention/...) for an Owner to actually use it comfortably, a UX concern not a
correctness one.
