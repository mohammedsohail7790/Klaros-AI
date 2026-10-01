# PHASE 17B-4: Real RLS Enforcement Readiness + Implementation — PROGRESS LOG

## Status: PHASE 17B-4 COMPLETE WITH LIMITATIONS (Round 15 — MCP credential authentication fixed following the klaros_discovery precedent; E2E suite 20/20; see §40 for the full completion-gate checklist and the one honest, coordinator-confirmed known limitation)

**Round 15 summary:** closed out §37d, the one item left open at the end of Round 14.
Extended `klaros_discovery` with a 6th narrow, read-only grant
(`mcp_client_credentials`: `id`/`tenant_id`/`token_hash`/`status` only), and — for the
first time in this phase — actually wired live application code
(`McpCredentialService.authenticate`) to use a real `klaros_discovery`-bound session for
the tenant-resolution step, with a genuine per-tenant `klaros_app` session for the real
work after that, and a safe, tested fallback for any environment that hasn't provisioned
`klaros_discovery` yet (including this codebase's own default test suite — a real
regression this round found and fixed in its own testing before landing, not after).
Validated with the same two-layer rigor as every other discovery grant: 12/12
database-level checks, plus a real end-to-end service-level proof (issue → authenticate
→ correct tenant resolved → `last_used_at` genuinely updated under real RLS → unknown
token fails closed → cross-tenant isolation intact). Full backend regression suite
re-run a third time, still exactly matching the Phase 17B-3 baseline shape (2117
passed, 1 pre-existing known flake, 12 skipped). **E2E suite: 20/20 passed, up from
19/20 — all 5 named systems (Medical Tourism, Website, MCP, Webhook, Agent) now clean.**
See §39 for the full write-up and §40 for the final, honest completion-gate checklist
and status declaration.

## Status (Round 14, superseded by Round 15 above — kept for history): PHASE 17B-4 NOT COMPLETE (Round 14 — full test-matrix gate mostly closed: regression suite clean, frontend clean, real E2E validation found and FIXED a critical login-breaking bug; one MCP finding remains open)

**Round 14 summary (this update):** moved into the full validation gate §36/§37 started
in Round 13. (1) **Full backend regression suite**, run against a genuinely
`alembic upgrade head`-ed real-Postgres database matching CI's own setup: **2117
passed, 1 failed (the same pre-existing known voice flake), 12 skipped** — identical
shape to the Phase 17B-3 baseline (2115/1/12), zero new regressions. (2) **Frontend
typecheck/tests/build**: all three clean (0 type errors, 89/89 tests, successful
production build) — confirming the coordinator's own prediction that this would be a
no-op given the frontend was never touched. (3) **Real E2E validation** of Medical
Tourism, Website, MCP, Webhook, and Agent, exercising the REAL application
service-layer code (not hand-written SQL) through the real restricted `klaros_app` role
against a genuinely RLS-enforcing database — and it found the single most severe bug of
this entire phase: **real RLS enforcement on `users` silently broke login,
registration, and the `get_current_user` dependency nearly every authenticated API
endpoint relies on**, plus a systemic, 61-file/203-call-site latent bug where
`session.commit()` silently drops tenant context for any later read on the same
session. Both were root-caused precisely, fixed in application code exactly per the
coordinator's instruction (three files: `app/db/session.py`, `app/api/deps.py`,
`app/services/auth_service.py` — zero policies touched), and re-verified working. A
third finding (MCP credential authentication's cross-tenant lookup) was found but
deliberately NOT band-aided — it needs a real design decision, not a quick patch — and
is honestly reported as the one remaining open item. See §37 for the full incident
write-up and §38 for what's left. **19 of 20 E2E checks now pass.**

## Status (Round 13, superseded by Round 14 above — kept for history): PHASE 17B-4 NOT COMPLETE (Round 13 — TABLE COVERAGE NOW 132/132 with FULL individual backfill complete; full test-matrix validation gate NOT yet started)

**Round 13 summary (this update) — the table-coverage rollout is done:** landed the
tenth and eleventh (final) RLS migrations, `0062` and `0063`, closing out all 26
remaining tables in a single round per the coordinator's explicit request. (1) `0062`
(13 tables) included `events` — a table that already carried a pre-existing
`discovery_select` policy from Round 4's `klaros_discovery` provisioning (created back
when `events` had no RLS at all, so the policy was inert). This migration is the first
time `events` gets real RLS, making that policy live for the first time. Per the
coordinator's explicit instruction, this was tested with extra care: a dedicated
real-Postgres check suite confirms an ordinary tenant session (`klaros_app`) gets normal
tenant-scoped isolation (sees only its own tenant's events, both directions, fail-closed
on no context) while `klaros_discovery` still gets its exact same narrow column-level
cross-tenant read (id/tenant_id/event_type/status/created_at, never `payload`, never any
DML) — the two mechanisms coexist correctly with zero interference, exactly as designed
in §11c. (2) `0063` (13 tables) is the final batch — after it, **all 132 of 132
TENANT_SCOPED tables have real, enforcing RLS**, confirmed via a live query against a
genuinely `alembic upgrade head`-ed fresh database (132 real, 0 audit-mode, 0 none).
(3) Both migrations passed a clean upgrade→downgrade→upgrade cycle and a separate clean
base→head run. (4) **Full backfill completed in the same round**: rather than leaving
partial spot-check debt for a future round (the normal pattern), this round ran enough
real-Postgres checks (148 total across three check-runs) to give **every one of the 26
newly-added tables an individual isolation proof**, and also backfilled Round 12's 10
outstanding leftover tables from `0061` — so as of this round, **all 132 tables have an
individual real-Postgres isolation proof, not just structural (`pg_policies`)
verification**. See §17k/§17l for the full breakdown and §35 for the final tally.

**Per the coordinator's explicit instruction, table coverage alone does NOT constitute
COMPLETE.** The required next phase — Medical Tourism/Website/MCP/Webhook/Agent E2E
validation, the full backend regression suite (compared against the Phase 17B-3
baseline), a frontend typecheck/tests/build confirmation, a secret scan, and a static
search for any RLS-bypass mechanism (BYPASSRLS grants, superuser-at-runtime use, a
blanket bypass flag, a fabricated system-tenant identity, a `system=True`-style
parameter, or a second competing tenant-context mechanism) — has NOT started as of this
log entry and is substantial, separate work. See §36 for what this round could and could
not fit in, honestly reported.

## Status (Round 12, superseded by Round 13 above — kept for history): PHASE 17B-4 NOT COMPLETE (Round 12 — ninth RLS batch landed + Round 11's leftover 9 tables individually verified)

**Round 12 summary (this update):** continued directly from Round 11's own next-steps
list. (1) Landed a ninth real RLS migration, `0061`, covering a reviewed batch of 15
more tables spanning five adjacent marketing/operations domains: Nurture (3:
`nurture_sequences`, `nurture_enrollments`, `nurture_activities`), Reactivation (2:
`reactivation_campaigns`, `reactivation_candidates`), SEO (3: `seo_pages`,
`seo_keywords`, `seo_opportunities`), Local/Reputation (3: `local_listings`,
`local_reviews`, `local_reputation_events`), and Vendor/Purchasing (4: `vendors`,
`vendor_bills`, `purchase_orders`, `purchase_order_items`) — all 15 newly RLS-enabled, no
audit-mode conversions needed anywhere. (2) Verified a clean `alembic upgrade head` →
`downgrade 0060` → `upgrade head` cycle on the long-lived migrated database (exact
pre-migration state restored, confirmed via `pg_policies`/`pg_class`), plus a separate
clean `base → head` run (all 61 migrations) on a brand-new database. (3) Ran a 52-check
real-Postgres suite: isolation spot-checks on 5 of this round's 15 new tables
(`nurture_sequences`, `seo_pages`, `local_listings`, `vendors`, `purchase_orders`)
**plus, per the coordinator's explicit instruction, individual isolation proofs for all
9 of Round 11's previously-only-structurally-verified tables** (`referral_programs`,
`referral_rewards`, `referrals`, `retention_campaigns`, `retention_enrollments`,
`retention_opportunities`, `outbound_sequences`, `outbound_steps`,
`outbound_activities`) — **52/52 passed**. Live application code for all 5 discovery
paths remains unrewired. See §35 for the updated running tally: **106 of 132**
tenant-scoped tables now have real, tested RLS — only 26 tables remain. Per the
coordinator's explicit reminder, once full table coverage is reached the next step is
the full test matrix (Medical Tourism/Website/MCP/Webhook/Agent E2E, full backend
regression, frontend confirmation, secret scan, static search for bypass mechanisms) —
NOT a COMPLETE claim before that work is done.

## Status (Round 11, superseded by Round 12 above — kept for history): PHASE 17B-4 NOT COMPLETE (Round 11 — eighth RLS batch landed + Round 10's leftover 9 tables individually verified)

**Round 11 summary (this update):** continued directly from Round 10's own next-steps
list. (1) Landed an eighth real RLS migration, `0060`, covering a reviewed batch of 13
more tables spanning two adjacent customer-lifecycle-marketing domains:
Retention/Referral (8: `referral_codes`, `referral_programs`, `referral_rewards`,
`referrals`, `retention_activities`, `retention_campaigns`, `retention_enrollments`,
`retention_opportunities`) and Outbound (5: `outbound_lists`, `outbound_contacts`,
`outbound_sequences`, `outbound_steps`, `outbound_activities`) — all 13 newly
RLS-enabled, no audit-mode conversions needed anywhere (fully retired since Round 9).
(2) Verified a clean `alembic upgrade head` → `downgrade 0059` → `upgrade head` cycle on
the long-lived migrated database (exact pre-migration state restored, confirmed via
`pg_policies`/`pg_class`), plus a separate clean `base → head` run (all 60 migrations)
on a brand-new database. (3) Ran a 52-check real-Postgres suite: isolation spot-checks
on 4 of this round's 13 new tables (`referral_codes`, `retention_activities`,
`outbound_lists`, `outbound_contacts`) **plus, per the coordinator's explicit
instruction, individual isolation proofs for all 9 of Round 10's previously-only-
structurally-verified tables** (`content_performance`, `content_publications`,
`content_variants`, `marketing_content`, `marketing_lead_sources`,
`morning_brief_insights`, `morning_brief_recommendations`, `customer_notes`,
`customer_signoffs`) — **52/52 passed**. Live application code for all 5 discovery paths
remains unrewired. See §35 for the updated running tally: **91 of 132** tenant-scoped
tables now have real, tested RLS.

## Status (Round 10, superseded by Round 11 above — kept for history): PHASE 17B-4 NOT COMPLETE (Round 10 — seventh RLS batch landed + Round 9's leftover 7 tables individually verified)

**Round 10 summary (this update):** continued directly from Round 9's own next-steps
list, same pattern as every round since 4 — now structurally simpler since audit-mode is
fully retired (§17g). (1) Landed a seventh real RLS migration, `0059`, covering a
reviewed batch of 13 more tables spanning three domains: Content/Marketing (7:
`content_assets`, `content_performance`, `content_publications`, `content_variants`,
`marketing_content`, `marketing_lead_sources`, `marketing_spend`), Morning Brief (3:
`morning_briefs`, `morning_brief_insights`, `morning_brief_recommendations`), and
Customer (3: `customer_feedback`, `customer_notes`, `customer_signoffs`) — all 13 newly
RLS-enabled, no audit-mode conversions needed (none remain anywhere). (2) Verified a
clean `alembic upgrade head` → `downgrade 0058` → `upgrade head` cycle on the long-lived
migrated database (exact pre-migration state restored, confirmed via `pg_policies`/
`pg_class`), plus a separate clean `base → head` run (all 59 migrations) on a brand-new
database. (3) Ran a 44-check real-Postgres suite: isolation spot-checks on 4 of this
round's 13 new tables (`content_assets`, `marketing_spend`, `morning_briefs`,
`customer_feedback`) **plus, per the coordinator's explicit instruction, individual
isolation proofs for all 7 of Round 9's previously-only-structurally-verified tables**
(`medical_tourism_provider_credentials`, `medical_tourism_provider_procedures`,
`medical_tourism_consultations`, `job_materials`, `job_tasks`, `job_qa`,
`job_attachments`) — **44/44 passed**. Live application code for all 5 discovery paths
remains unrewired. See §35 for the updated running tally: **78 of 132** tenant-scoped
tables now have real, tested RLS.

## Status (Round 9, superseded by Round 10 above — kept for history): PHASE 17B-4 NOT COMPLETE (Round 9 — sixth RLS batch lands ALL 7 Medical Tourism tables + closes out audit-mode entirely + Round 8's leftover 9 tables individually verified)

**Round 9 summary (this update):** continued directly from Round 8's own next-steps
list. (1) Landed a sixth real RLS migration, `0058`, converting **all 7 Medical Tourism
tables** from audit-mode to real enforcement (`medical_tourism_providers`,
`medical_tourism_provider_credentials`, `medical_tourism_procedures`,
`medical_tourism_provider_procedures`, `medical_tourism_patient_leads`,
`medical_tourism_consultations`, `medical_tourism_referral_commissions`) — **this closes
out every table that started this phase in Phase-0 audit-mode; zero tables remain in
permissive audit-mode anywhere in the schema**, plus 6 newly-enforced Jobs/Finance tables
(`job_costs`, `job_materials`, `job_tasks`, `job_qa`, `job_attachments`, `refunds`). Per
the coordinator's explicit instruction, Medical Tourism — the vertical this whole
security track has been building toward — got the exact same full rigor as every other
batch, not a rushed or abbreviated treatment: 4 of the 6 tables in this round's real-
Postgres spot-check sample were Medical Tourism tables (not just 1 for form's sake).
(2) Verified a clean `alembic upgrade head` → `downgrade 0057` → `upgrade head` cycle on
the long-lived migrated database (exact pre-migration state restored, confirmed via
`pg_policies`/`pg_class`), plus a separate clean `base → head` run (all 58 migrations) on
a brand-new database. (3) Ran a 60-check real-Postgres suite: isolation spot-checks on 6
of this round's 13 new tables — 4 Medical Tourism (`medical_tourism_providers`,
`medical_tourism_procedures`, `medical_tourism_patient_leads`,
`medical_tourism_referral_commissions`) plus 2 Jobs/Finance (`job_costs`, `refunds`) —
**plus, per the coordinator's explicit instruction, individual isolation proofs for all 9
of Round 8's previously-only-structurally-verified tables** (`mcp_client_credentials`,
`organization_vertical_extensions`, `website_versions`, `website_pages`,
`website_sections`, `cash_forecast_items`, `payment_allocations`, `credit_notes`,
`credit_note_line_items`) — **60/60 passed**. Live application code for all 5 discovery
paths remains unrewired. See §35 for the updated running tally: **65 of 132**
tenant-scoped tables now have real, tested RLS — audit-mode is fully retired, and every
remaining table (67 of 132) simply has no RLS at all yet.

## Status (Round 8, superseded by Round 9 above — kept for history): PHASE 17B-4 NOT COMPLETE (Round 8 — fifth RLS batch landed + Round 7's leftover 8 tables individually verified)

**Round 8 summary (this update):** continued directly from Round 7's own next-steps list,
same pattern as every round since 4. (1) Landed a fifth real RLS migration, `0057`,
covering a reviewed batch of 13 more tables: 7 already-audit-mode tables converted in
place, completing every remaining audit-mode table EXCEPT the 7 Medical Tourism tables
(`mcp_tool_exposures`, `mcp_client_credentials`, `organization_vertical_extensions`,
`websites`, `website_versions`, `website_pages`, `website_sections`), and 6 finance-domain
tables with no RLS at all before (`cash_forecasts`, `cash_forecast_items`, `payouts`,
`payment_allocations`, `credit_notes`, `credit_note_line_items`). (2) Verified a clean
`alembic upgrade head` → `downgrade 0056` → `upgrade head` cycle on the long-lived
migrated database (exact pre-migration state restored, confirmed via `pg_policies`/
`pg_class`), plus a separate clean `base → head` run (all 57 migrations) on a brand-new
database. (3) Ran a 48-check real-Postgres suite: isolation spot-checks on 4 of this
round's 13 new tables (`websites`, `cash_forecasts`, `payouts`, `mcp_tool_exposures`)
**plus, per the coordinator's explicit instruction, individual isolation proofs for all 8
of Round 7's previously-only-structurally-verified tables** (`discovery_turns`,
`business_blueprints`, `blueprint_sections`, `blueprint_claims`, `business_journeys`,
`appointments`, `call_sessions`, `campaign_leads`) — **48/48 passed**. Live application
code for all 5 discovery paths remains unrewired, unchanged from the coordinator's
explicit instruction to keep it deferred. See §35 for the updated running tally: **52 of
132** tenant-scoped tables now have real, tested RLS — only the 7 Medical Tourism tables
remain in audit-mode, and everything else outstanding has no RLS at all yet.

## Status (Round 7, superseded by Round 8 above — kept for history): PHASE 17B-4 NOT COMPLETE (Round 7 — fourth RLS batch landed + Round 6's leftover 8 tables individually verified)

**Round 7 summary (this update):** continued directly from Round 6's own next-steps list,
same pattern as every round since 4. (1) Landed a fourth real RLS migration, `0056`,
covering a reviewed batch of 12 more tables: the Business Discovery/Blueprint/Journey
domain's 6 already-audit-mode tables converted in place (`discovery_sessions`,
`discovery_turns`, `business_blueprints`, `blueprint_sections`, `blueprint_claims`,
`business_journeys`), and 6 tables with no RLS at all before, spread across ops/marketing/
comms (`appointments`, `call_sessions`, `campaigns`, `campaign_leads`,
`notification_preferences`, `workers`). (2) Verified a clean `alembic upgrade head` →
`downgrade 0055` → `upgrade head` cycle on the long-lived migrated database (exact
pre-migration state restored, confirmed via `pg_policies`/`pg_class`), plus a separate
clean `base → head` run (all 56 migrations) on a brand-new database. (3) Ran a 48-check
real-Postgres suite: isolation spot-checks on 4 of this round's 12 new tables
(`discovery_sessions`, `campaigns`, `notification_preferences`, `workers`) **plus, per
the coordinator's explicit instruction, individual isolation proofs for all 8 of Round
6's previously-only-structurally-verified tables** (`company_memories`,
`integration_connections`, `recommendation_runs`, `recommendations`, `quotes`,
`quote_line_items`, `jobs`, `notifications`) — **48/48 passed**. Live application code for
all 5 discovery paths remains unrewired, unchanged from the coordinator's explicit
instruction to keep it deferred. See §35 for the updated running tally: **39 of 132**
tenant-scoped tables now have real, tested RLS.

## Status (Round 6, superseded by Round 7 above — kept for history): PHASE 17B-4 NOT COMPLETE (Round 6 — third RLS batch landed + a total-count arithmetic correction + Round 5's leftover 6 tables individually verified)

**Round 6 summary (this update):** continued directly from Round 5's own next-steps list.
(1) **Found and corrected a real arithmetic error present in this log since Round 2**: the
headline "125 TENANT_SCOPED tables" total never actually matched the round's own
component numbers (Round 2 itself reported "32 already audit-mode... and 100 have no RLS
at all", which sums to 132, not 125 — the discrepancy was silently carried forward
through Rounds 3-5 without anyone re-adding the two numbers). A fresh live query against
a genuinely `alembic upgrade head`-ed database this round confirms the correct total is
**132** tenant-scoped-shaped tables (verified independently: 15 real + 26 audit-mode + 91
no-RLS = 132, and this also exactly reconciles backward against every prior round's own
stated conversion counts — see §4-9's Round 6 addendum for the full reconciliation). All
tallies from this round forward use 132 as the denominator; §35 keeps the historical
125-based entries visible but flags them as the now-corrected figure. This does not
change any actual RLS/security finding from Rounds 2-5 — only the headline denominator
was wrong, not the per-table classification work itself. (2) Landed a third real RLS
migration, `0055`, covering a reviewed batch of 12 more tables: the original Phase-0
"tier 1" five's last four audit-mode members (`approval_requests`, `audit_logs`,
`company_memories`, `integration_connections`) plus `recommendation_runs`/
`recommendations` converted in place (6 total), and 6 ordinary TENANT_SCOPED tables with
no RLS at all before (`leads`, `quotes`, `quote_line_items`, `jobs`, `notifications`,
`contracts`). (3) Verified a clean migration cycle exactly as in prior rounds — both
`upgrade head → downgrade 0054 → upgrade head` on a long-lived migrated database and a
separate clean `base → head` on a brand-new database. (4) Ran a 40-check real-Postgres
suite: isolation spot-checks (both directions, fail-closed, blocked-UPDATE-is-a-real-no-op)
on 4 of this round's 12 new tables (`audit_logs`, `leads`, `contracts`,
`approval_requests`) **plus, per the coordinator's explicit instruction, individual
isolation proofs for all 6 of Round 5's previously-only-structurally-verified tables**
(`agent_versions`, `agent_tool_permissions`, `agent_execution_steps`,
`automation_executions`, `automation_execution_steps`, `invoice_line_items`) — **40/40
passed**. Live application code for all 5 discovery paths remains unrewired, exactly as
the coordinator asked to keep deferred. See §35 for the updated running tally.

## Status (Round 5, superseded by Round 6 above — kept for history): PHASE 17B-4 NOT COMPLETE (Round 5 — second RLS batch landed + discovery role extended to all 5 paths' grants)

**Round 5 summary (this update):** picked up exactly where §34 (Round 4's prerequisites
list) left off. (1) Extended `klaros_discovery`'s column-level grants
(`backend/scripts/db/provision_discovery_role.py`) to the other 4 discovery paths'
tables — `automations`/`automation_versions`, `agents`/`agent_versions`, `organizations`,
`agent_executions` — using the exact field inventories from §10/§11, so all 5 real
discovery paths now have database-level grants (up from 1/5). (2) Discovered and fixed a
real design gap while doing this: 3 of the newly-granted tables (`automations` already,
`agents`/`agent_versions`/`agent_executions` as of this round's own migration) carry real
enforcing RLS, so a bare column GRANT alone would have left `klaros_discovery` able to
read the columns but see ZERO rows (its session never calls `set_tenant_context`, so
`current_tenant_id()` is NULL for it, and the ordinary `tenant_select` policy would
exclude every row) — silently breaking cross-tenant discovery rather than narrowing it.
Fixed by having the provisioning script also create a `discovery_select` policy
(`FOR SELECT TO klaros_discovery USING (true)`) on every granted table — scoped to that
one named role only, harmless/inert on a table with no RLS enabled (`organizations`,
`events`) — see §11c below for the full reasoning and why this lives in the idempotent
Python script rather than an Alembic migration. (3) Landed a second real RLS migration,
`0054`, covering a reviewed batch of 10 more tables: 5 already-audit-mode tables
converted in place (`agents`, `agent_versions`, `agent_tool_permissions`,
`agent_executions`, `agent_execution_steps`) and 5 tables with no RLS at all before
(`automation_versions`, `automation_executions`, `automation_execution_steps`,
`invoice_line_items`, `payments`) — chosen deliberately to overlap with the discovery-grant
work above so the new `discovery_select` policy path would be exercised for real, not
left theoretical. (4) Verified a clean `alembic upgrade head` → `downgrade 0053` →
`upgrade head` cycle on a genuinely Alembic-migrated database (never touched by
`conftest.py`), confirmed the exact pre-migration state is restored on downgrade
(audit-mode tables get their old permissive policy back, newly-enforced tables get RLS
fully disabled again), and separately re-verified a clean `base → head` upgrade on a
brand-new, never-before-touched database. (5) Ran a 29-check real-Postgres spot-check
suite (not the full 43-check treatment on every table, per the coordinator's "reviewed
batch" instruction) covering SELECT isolation both directions, fail-closed NULL context,
blocked cross-tenant UPDATE as a genuine 0-row no-op, on a 4-table sample spanning both
halves of the batch (`agents`, `agent_executions`, `automation_versions`, `payments`),
plus all-new checks for the 5 newly-completed discovery grants (cross-tenant read
succeeds, ungranted columns/tables still correctly refused, no DML possible anywhere) —
**29/29 passed**. Live application code for all 5 discovery paths remains unrewired
(deliberately, unchanged from Round 4's decision — still correctly out of scope). See
§35 for the updated running per-table tally.

## Status (Round 4, superseded by Round 5 above — kept for history): PHASE 17B-4 NOT COMPLETE (Round 4 — first real migrations landed + discovery role proven end-to-end)

Round 1 was read-only reconnaissance. Round 2 produced the live-Postgres catalog
classification, the 5 discovery-query field inventories, and the tested CashForecast
fix. Round 3 made the system-discovery architecture decision and proved the
tenant-isolation RLS policy pattern via raw SQL (43/43 checks). **Round 4 (this update)
converts that proof into two real, committed-to-the-repo (but not yet git-committed —
still just working-tree files) Alembic migrations** (`0052` the helper function, `0053`
real enforcement on the 5 proven tables), verifies a clean upgrade→downgrade→upgrade
cycle on a genuinely Alembic-migrated database, re-runs the full 43-check suite against
the actual migrated schema (not raw SQL — identical 43/43 result), and implements +
proves the `klaros_discovery` role end-to-end for the first of the 5 discovery paths
(EventBus) — 15/15 new checks passed, including the real column-level narrowing
(`payload` genuinely unreadable) that raw-SQL proof alone couldn't fully demonstrate.
A third, independent confirmation of the conftest-vs-real-migration pitfall was also
found this round (see §19-29 update below). See §35 for the running per-table tally the
coordinator asked for.

## 1. Executive Summary

Round 2 completed 3 of the 4 concrete deliverables the coordinator asked for: (1) a real
live-Postgres catalog classification of all 136 public tables — TENANT_SCOPED=125 (7 of
which are also read cross-tenant by system discovery sweeps), GLOBAL_SHARED=2,
AUTH_BOUNDARY=1, SYSTEM=1, **UNKNOWN=0**; (2) exact current-code field inventories for
all 5 cross-tenant discovery queries; (3) the CashForecast ownership bug is now fixed,
covered by a real-Postgres regression test, and the full existing CashForecast/finance
test suite still passes. Item 4 (keep the log current) is this document. No RLS policy
or migration was written this round — correctly deferred per the coordinator's explicit
instruction, since the system-discovery architecture decision (Option A vs B) still needs
to be made from the field inventories below before policies can be designed safely.

A real, previously-unverified methodological pitfall was found and resolved during this
round: `backend/tests/conftest.py`'s autouse `_reset_database` fixture builds the test
schema via SQLAlchemy's `Base.metadata.create_all()` and then hand-stamps
`alembic_version` to the current head — it does **not** run real Alembic migrations. That
means the RLS DDL (`ENABLE ROW LEVEL SECURITY` / `CREATE POLICY`), which lives only in
Alembic migration files and not in any SQLAlchemy `Table`/`Column` definition, is never
applied when a database is set up this way, even against real Postgres, even though the
ordinary pytest suite otherwise looks like a legitimate "real Postgres" run. This is not
a product bug — audit-mode RLS genuinely works when applied via real
`alembic upgrade head` (verified below) — but it means **the existing pytest suite does
not, and structurally cannot, exercise any RLS policy behavior**, audit-mode or future
real enforcement, unless a test explicitly migrates its own separately-provisioned
database via Alembic rather than relying on `conftest.py`'s fixture. This is an important
fact for designing the RLS test matrix in a future round (see §34).

**Round 3 completed all 4 of the coordinator's follow-up items:** (1) made and
documented the system-discovery architecture decision — Option A, a dedicated
`klaros_discovery` role with column-level grants + role-scoped policies, not a SECURITY
DEFINER function (§11); (2) designed the tenant-isolation RLS policy pattern and
validated it against real Postgres, under the actual restricted `klaros_app` role, on 5
representative tables spanning every relevant classification — 43/43 checks passed,
covering SELECT/INSERT/UPDATE/DELETE isolation, fail-closed NULL/empty/malformed
context, `WITH CHECK` tenant-reassignment prevention, connection-pool reuse,
transaction-rollback safety, and concurrent-tenant isolation (§17b); (3) determined the
0048 downgrade issue reported in Round 2 does **not** block this phase — it was a false
alarm from the same conftest-vs-real-migration root cause already found once, and a real
upgrade→downgrade→upgrade cycle through 0048 on a properly Alembic-migrated database is
clean (§19-29); (4) this document. Migrations for the full ~95-table rollout and the
discovery mechanism's actual implementation remain undone, correctly, per instruction to
prove the pattern first.

## 2. Starting / Final HEAD

- Starting HEAD (this round): `af4937e403e47cdc141f2db349dfcc46a3df6c4b`
- Final HEAD (end of this round): `af4937e403e47cdc141f2db349dfcc46a3df6c4b` (unchanged — no commits made)

## 3. Git Status

- Start of Round 4: the same pre-existing modified files from Phases 17B-1/2/2R/3, plus
  this log file and Round 2's CashForecast edits (`backend/app/services/
  cash_forecast_service.py`, `backend/app/api/v1/cash.py`,
  `backend/tests/test_tenant_context_cash_forecast_service_phase17b2r.py`).
- End of Round 4: the same, **plus 3 new untracked files**:
  - `backend/alembic/versions/0052_rls_tenant_context_function.py` (new migration)
  - `backend/alembic/versions/0053_rls_tier1_real_enforcement.py` (new migration)
  - `backend/scripts/db/provision_discovery_role.py` (new script)
  No `git add`/`commit`/`push` was run at any point. Nothing staged or committed
  (`git diff --cached --stat` empty).
- End of Round 5: the same, **plus 1 more new untracked file**:
  - `backend/alembic/versions/0054_rls_tier2_real_enforcement.py` (new migration)
  and `backend/scripts/db/provision_discovery_role.py` (already untracked from Round 4)
  further edited in place (still untracked, not a new file). Still no `git add`/
  `commit`/`push` run at any point this round. `git diff --cached --stat` still empty.
- End of Round 6: the same, **plus 1 more new untracked file**:
  - `backend/alembic/versions/0055_rls_tier3_real_enforcement.py` (new migration)
  Still no `git add`/`commit`/`push` run at any point this round. `git diff --cached
  --stat` still empty. HEAD still `af4937e403e47cdc141f2db349dfcc46a3df6c4b`.
- End of Round 7: the same, **plus 1 more new untracked file**:
  - `backend/alembic/versions/0056_rls_tier4_real_enforcement.py` (new migration)
  Still no `git add`/`commit`/`push` run at any point this round. `git diff --cached
  --stat` still empty. HEAD still `af4937e403e47cdc141f2db349dfcc46a3df6c4b`.
- End of Round 8: the same, **plus 1 more new untracked file**:
  - `backend/alembic/versions/0057_rls_tier5_real_enforcement.py` (new migration)
  Still no `git add`/`commit`/`push` run at any point this round. `git diff --cached
  --stat` still empty. HEAD still `af4937e403e47cdc141f2db349dfcc46a3df6c4b`.
- End of Round 9: the same, **plus 1 more new untracked file**:
  - `backend/alembic/versions/0058_rls_tier6_medical_tourism_and_jobs.py` (new migration)
  Still no `git add`/`commit`/`push` run at any point this round. `git diff --cached
  --stat` still empty. HEAD still `af4937e403e47cdc141f2db349dfcc46a3df6c4b`.
- End of Round 10: the same, **plus 1 more new untracked file**:
  - `backend/alembic/versions/0059_rls_tier7_real_enforcement.py` (new migration)
  Still no `git add`/`commit`/`push` run at any point this round. `git diff --cached
  --stat` still empty. HEAD still `af4937e403e47cdc141f2db349dfcc46a3df6c4b`.
- End of Round 11: the same, **plus 1 more new untracked file**:
  - `backend/alembic/versions/0060_rls_tier8_real_enforcement.py` (new migration)
  Still no `git add`/`commit`/`push` run at any point this round. `git diff --cached
  --stat` still empty. HEAD still `af4937e403e47cdc141f2db349dfcc46a3df6c4b`.
- End of Round 12: the same, **plus 1 more new untracked file**:
  - `backend/alembic/versions/0061_rls_tier9_real_enforcement.py` (new migration)
  Still no `git add`/`commit`/`push` run at any point this round. `git diff --cached
  --stat` still empty. HEAD still `af4937e403e47cdc141f2db349dfcc46a3df6c4b`.
- End of Round 13: the same, **plus 2 more new untracked files**:
  - `backend/alembic/versions/0062_rls_tier10_real_enforcement.py` (new migration)
  - `backend/alembic/versions/0063_rls_tier11_final_batch.py` (new migration)
  Still no `git add`/`commit`/`push` run at any point this round. `git diff --cached
  --stat` still empty. HEAD still `af4937e403e47cdc141f2db349dfcc46a3df6c4b`.
- End of Round 14: the same, **plus 3 modified application files** (no new migrations —
  this round's fixes are all application code, per §37): `backend/app/db/session.py`,
  `backend/app/api/deps.py`, `backend/app/services/auth_service.py` (all `M`, not `??`
  — these are pre-existing tracked files this phase has never touched before). Still no
  `git add`/`commit`/`push` run. HEAD still `af4937e403e47cdc141f2db349dfcc46a3df6c4b`.
- End of Round 15: the same, **plus 2 more modified files and 1 new untracked file**:
  `backend/app/core/config.py` (`M`), `backend/app/services/mcp_service.py` (`M`),
  `backend/scripts/db/provision_discovery_role.py` (already untracked from Round 4,
  further edited — still `??`, not a new file). Still no `git add`/`commit`/`push` run
  at any point across all 15 rounds. HEAD still `af4937e403e47cdc141f2db349dfcc46a3df6c4b`.

## 4-9. Table Inventory / Classification / Policy Architecture / System Discovery Architecture

**Round 6 addendum — total-count correction:** the "125 TENANT_SCOPED tables" headline
below has been repeated in every round's report since Round 2, but never actually matched
that same round's own component breakdown — Round 2 itself wrote "32 already have
Phase-0-era audit-mode RLS... and 100 have no RLS at all yet," which sums to **132**, not
125. Nobody re-added the two numbers in Rounds 3-5 before restating "125" as the total.
Round 6 re-derived the true count live: a fresh query (`pg_class`/`information_schema.
columns`/`pg_policies`) against a genuinely `alembic upgrade head`-ed database (head =
`0055` at time of query) finds exactly **132** tables with a `tenant_id` column, split
15 real-RLS + 26 audit-mode + 91 no-RLS. This independently reconciles perfectly backward
against every prior round's own stated *conversion* counts (which were always internally
correct, only the fixed denominator was wrong): Round 4 converted 5 tables from an
original 32-audit/100-no-RLS split (32+100=132), leaving 31 audit/96 no-RLS; Round 5
converted 10 more (5 audit, 5 no-RLS), leaving 26 audit/91 no-RLS — exactly matching this
round's live count. **No table was missed or miscounted in the actual classification
work** — the per-table lists in §35 across every round have always been accurate; only
the headline total (125 vs. the correct 132) was arithmetically wrong and is corrected
from this round forward. Total public tables (136), GLOBAL_SHARED (2:
`vertical_extensions`, `integration_provider_catalog`), AUTH_BOUNDARY (1:
`organizations`), and SYSTEM (1: `alembic_version`) are all unaffected and still correct
(136 = 132 + 2 + 1 + 1).

**Table inventory and classification: DONE this round (first full pass, real Postgres).**

Methodology: started a disposable PostgreSQL 16 instance via `pgserver` (already vendored
in `backend/.venv`, Docker unavailable in this sandbox, matching every prior phase's own
documented approach), created a fresh database, ran the real
`alembic upgrade head` (all 51 migrations, exit 0, no errors), then queried
`pg_class`/`information_schema.columns`/`pg_indexes`/`pg_policies` directly. (A first
attempt using a database that pytest's `conftest.py` had already touched gave a false
"RLS is disabled everywhere" reading — diagnosed and explained in §1 above; a cleanly
migrated database was used for the real numbers below.)

**136 public tables found.** Classification:

| Class | Count |
|---|---|
| TENANT_SCOPED (has `tenant_id`, ordinary per-tenant table) | 125 |
| ...of which also read cross-tenant by a system discovery sweep (see §10 below) | 7 |
| GLOBAL_SHARED (no `tenant_id`, platform-wide catalog) | 2 (`vertical_extensions`, `integration_provider_catalog`) |
| AUTH_BOUNDARY (the tenant root itself) | 1 (`organizations`) |
| SYSTEM (Alembic's own bookkeeping table) | 1 (`alembic_version`) |
| **UNKNOWN** | **0** |

Of the 125 TENANT_SCOPED tables, **32 already have Phase-0-era audit-mode RLS**
(`ENABLE ROW LEVEL SECURITY`, not `FORCE`, permissive `USING(true)/WITH CHECK(true)`
policy named `tenant_isolation_audit_policy`, introduced incrementally by migrations
0040/0043/0044/0045/0046/0048/0049/0050/0051 — the pattern each of those migrations'
docstrings already call out as "same treatment as every table since 0040"), and **100
have no RLS at all yet**. `tenant_id` is indexed on every one of the 132 tables that has
the column, and is `NOT NULL` on 131 of them — the one exception is `webhook_events`,
where `tenant_id` is nullable (a webhook can arrive before tenant resolution; this needs
explicit policy-design attention in the next round, not a blind NOT NULL conversion, per
the brief's own instruction not to silently change data-model semantics). No table has
`FORCE ROW LEVEL SECURITY` set (confirmed — matches Phase 17B-3's finding, still true).

Full per-table classification (table, class, current RLS state, tenant_id nullability,
tenant_id index presence) is recorded in this round's raw output; the summary above is
complete enough to plan from, and the full 136-row table can be regenerated on demand
from `pg_class`/`information_schema.columns` against any freshly-migrated instance — it
was not pasted in full here to keep this document navigable, but every number above is a
live-query result, not an estimate, and the 32-table and 100-table name lists were
captured this round (see the raw JSON note in §34 for where to re-derive them if needed).

**Policy architecture / System discovery architecture: informed by §10 below, decision
NOT YET MADE.** Deferred correctly to the next round per explicit instruction.

## 10. System Discovery Query Field Inventories (DONE this round — pure reading)

Exact current-code read of all 5 cross-tenant discovery paths, as they exist at HEAD:

**1. Automation discovery** — `backend/app/services/automation_service.py:332`,
`AutomationService.check_and_dispatch_scheduled(tenant_id=None, ...)`. When called with
`tenant_id=None` (the real background worker's sweep-all-tenants tick), it calls
`set_tenant_context(session, None)` (a no-op/null context) and runs:
```python
select(AutomationVersion, Automation)
  .join(Automation, Automation.published_version_id == AutomationVersion.id)
  .where(Automation.status == ENABLED, AutomationVersion.trigger_type == SCHEDULE)
```
— i.e. it selects **every column** of both `Automation` and `AutomationVersion` (full
ORM row, not a narrow field list) for every enabled, schedule-triggered automation across
every tenant, plus a second query for `Organization.timezone` for the set of tenant_ids
found. The per-candidate dedup check and the actual dispatch (`start_execution`) each
open their own session and correctly call
`set_tenant_context(session, automation.tenant_id)` — only the initial candidate-listing
read is genuinely cross-tenant.

**2. Agent discovery** — `backend/app/services/agent_trigger_service.py:89`,
`AgentTriggerService.check_and_dispatch_scheduled(tenant_id=None, ...)`. When
`tenant_id is None`, **no `set_tenant_context` call is made at all** for the discovery
session (the code explicitly skips it — see the inline comment at line ~105). Query:
```python
select(Agent, AgentVersion).join(AgentVersion, Agent.current_version_id == AgentVersion.id)
```
— again the full ORM row for both tables, for every agent with a current version,
filtered in Python afterward for `triggers["schedule"]["enabled"]`, plus a second query
for `Organization.timezone`. Dispatch itself (`self._reasoning.start(agent.tenant_id, ...)`)
is per-tenant.

**3. Morning Brief organization discovery** —
`backend/app/services/morning_brief_service.py:604`,
`MorningBriefService.check_and_generate_scheduled()`. Discovery session reads **only**
`Organization` (already documented in-code as the intentional design, Phase 17B-3):
```python
select(Organization).where(Organization.morning_brief_enabled.is_(True))
```
No tenant context set, but it never touches a genuinely tenant-owned table in that
session — full `Organization` row (all columns) is read, not a narrow field list. Every
subsequent per-org check (`MorningBrief` existence lookup) and the actual `generate()`
call are correctly done in their own session with `set_tenant_context(session, org.id)`.

**4. Agent Recovery discovery** —
`backend/app/services/agent_recovery_service.py:215`, `_find_stale_candidates`. This is
the **narrowest** of the five already: when `tenant_id is None`, no context is set, and
the query selects only:
```python
select(AgentExecution.id).where(
    or_(
        and_(status == RUNNING, or_(lease_expires_at IS NULL, lease_expires_at < now)),
        and_(status == PENDING, created_at < pending_orphan_cutoff),
    )
).order_by(created_at).limit(limit)
```
— just `AgentExecution.id`, nothing else, `LIMIT`-bounded. The claim step
(`_claim`, a single conditional `UPDATE ... WHERE id = :id AND status = ...` keyed only
by execution_id) and the actual recovery (`_recover_one`) resolve and stamp the real
tenant afterward.

**5. EventBus stuck-event discovery** — `backend/app/events/bus.py:348`,
`EventBus.reconcile_stuck_events()`. Always a cross-tenant scan by design (no `tenant_id`
parameter exists on this method at all), no context set (explicitly documented in-code as
deferred to this exact phase). Query:
```python
select(Event).where(
    Event.status == PUBLISHED,
    Event.event_type.in_(subscribed_types),
    Event.created_at < cutoff_dt,
).limit(limit)
```
— full `Event` ORM row (all columns, including payload), then narrows in Python to
`{event_id, tenant_id, event_type, ...}` before re-enqueueing. This is the one path
whose current code reads more than it needs (a full row, including potentially
sensitive event payload) purely for a metadata-level operation — worth tightening when
the narrow discovery mechanism is designed, not just replicating today's `SELECT *`
shape.

**Implication for system-discovery design (next round):** all 5 paths today either skip
`set_tenant_context` entirely or call it with `None`, and today's code always
`SELECT`s full ORM rows rather than the narrow field lists the brief asks for. Every path
correctly re-scopes to a real per-tenant session before any mutation. The narrowest
already (Agent Recovery, `id`-only) is a good existence proof that a minimal field
mechanism is achievable; the widest (Automation/Agent discovery, full joined ORM rows)
will need explicit narrowing regardless of whether Option A (role) or Option B (SECURITY
DEFINER function) is chosen.

## 11. System Discovery Architecture Decision — DECIDED this round

**Decision: Option A — a dedicated, narrowly-scoped PostgreSQL role (`klaros_discovery`),
using native column-level `GRANT SELECT (...)` plus a per-table permissive RLS policy
scoped `TO klaros_discovery` only — not Option B (SECURITY DEFINER function).**

Reasoning, grounded in the real §10 field inventories (not the brief's illustrative
minimal field lists):

- **Why Option A over Option B.** A SECURITY DEFINER function per discovery path (5
  functions) would need its own `search_path` pinning, its own hand-written column
  allow-list in its `RETURNS TABLE(...)` signature, and its own audit review of function
  body correctness each time a discovery query changes — procedural code with a security
  boundary that has to be read to be trusted. Column-level `GRANT` + a role-scoped policy
  achieves the identical guarantee (this role can read only these exact columns, only
  cross-tenant, never mutate anything) using pure declarative Postgres privilege
  primitives, fully introspectable via `information_schema.column_privileges` and
  `pg_policies` (`roles` column) without reading any procedural code at all — more
  auditable, less surface, and there is no case among the 5 real queries (all simple
  filtered `SELECT`s, no need for row-level computation only a function body could
  express) where a function buys anything a grant doesn't.
- **`klaros_discovery`: LOGIN, NOSUPERUSER, NOBYPASSRLS, NOCREATEDB, NOCREATEROLE,
  NOREPLICATION, not a table owner** (same restricted shape as `klaros_app`, per the
  brief's own requirement that the discovery identity be incapable of arbitrary tenant
  access). It receives **zero** DML grants (no INSERT/UPDATE/DELETE anywhere, ever) and
  column-level SELECT **only** on the specific columns each of the 5 paths actually uses:
  - `automations(id, tenant_id, status, published_version_id)`,
    `automation_versions(id, trigger_type, trigger_config, condition, steps)`
  - `agents(id, tenant_id, status, current_version_id)`,
    `agent_versions(id, triggers, instructions_snapshot)`
  - `organizations(id, timezone, morning_brief_enabled, morning_brief_local_time, morning_brief_timezone)`
  - `agent_executions(id)` — already this narrow in the real code; no change needed
  - `events(id, tenant_id, event_type, status, created_at)` — deliberately narrower than
    today's real `SELECT *` (which includes `payload`); nothing in
    `reconcile_stuck_events` reads the payload, so this is a real tightening, not just a
    restatement of current behavior
  - Each of these tables also gets one additional permissive policy,
    `FOR SELECT TO klaros_discovery USING (true)`, alongside its real per-tenant policy —
    scoped to that single named role only, so no other role (including `klaros_app`) is
    affected and no generic bypass is created.
- **Why not the brief's bare illustrative field lists for Automation/Agent discovery.**
  `AutomationService.start_execution` and the equivalent Agent dispatch path consume
  `version.condition` and `version.steps` (or `version.triggers`/
  `instructions_snapshot`) from the very objects the discovery query fetched — see §10's
  exact code trace. Restricting discovery to bare `id`/`status`/`tenant_id` metadata,
  matching the brief's illustrative list literally, would force a second, per-tenant
  re-fetch of the same row immediately after discovery before dispatch could proceed —
  a real, riskier restructuring of `check_and_dispatch_scheduled` in both services just
  to hit an illustrative minimalism target, not a safety improvement (the extra columns
  are non-sensitive trigger/condition/step configuration, not customer data, financial
  data, or credentials). Matching the grant list to *actual downstream field use* is the
  smaller, safer change the coordinator asked me to judge for — implementing it is next
  round's work, not done yet.
- **Implementation update (Round 4): started, one path done end-to-end.** See §11b below
  — `klaros_discovery` is now a real, tested provisioning script with column-level grants
  for `events` (the EventBus path), proven end-to-end at the database level. The other 4
  discovery paths' grants (Automation, Agent, Morning Brief, Agent Recovery) and the
  application-code query rewrites at all 5 call sites (including EventBus's own live
  `bus.py` code, which still runs its original `select(Event)` query today — only the
  role/grant mechanism was proven this round, the live code path was not yet rewired to
  use it) remain next round's work.

## 11b. System Discovery Role — Implemented and Proven End-to-End for EventBus (Round 4)

**Done this round:** `backend/scripts/db/provision_discovery_role.py` — a new script
mirroring `provision_app_role.py`'s idempotent pattern, provisioning `klaros_discovery`
(LOGIN, NOSUPERUSER, NOBYPASSRLS, NOCREATEDB, NOCREATEROLE, NOREPLICATION — identical
restricted shape to `klaros_app`) with:
- `GRANT CONNECT`/`GRANT USAGE ON SCHEMA public` (baseline connectivity only).
- **Column-level** `GRANT SELECT (id, tenant_id, event_type, status, created_at) ON
  events` — deliberately excluding `payload` (and every other column), since nothing in
  `EventBus.reconcile_stuck_events()`'s real code (§10's trace) reads it; this is the
  real tightening the coordinator asked for, not just a mechanism proof.
- **Zero** INSERT/UPDATE/DELETE grants anywhere, on any table.
- **No** `ALTER DEFAULT PRIVILEGES` — unlike `klaros_app`, a table added by a future
  migration is NOT automatically readable by this role; every table must be an explicit,
  reviewed addition to the script's `_TABLE_COLUMN_GRANTS` dict.
- Only `events` granted this round — the other 4 discovery paths' tables
  (`automations`/`automation_versions`, `agents`/`agent_versions`, `organizations`,
  `agent_executions`) are intentionally not yet in the grant list.

**Validated against real Postgres (disposable instance, fresh Alembic-migrated
database, role provisioned via the real script — 15/15 checks passed):**
- Role attributes: genuinely `NOSUPERUSER`/`NOBYPASSRLS`/`NOCREATEDB`/`NOCREATEROLE`
  (direct `pg_roles` query).
- Can read the 5 granted columns **across both tenant A's and tenant B's** `events` rows
  in one query — this is the intended behavior (genuine, intentional cross-tenant
  discovery), not a bug.
- **Cannot** `SELECT payload` (explicit column query) — real Postgres
  `InsufficientPrivilegeError`, i.e. the database itself refuses, not application-layer
  discipline.
- **Cannot** `SELECT *` either (implicitly touches the ungranted `payload` column) — same
  error.
- **Cannot** UPDATE, DELETE, or INSERT into `events` at all — three separate real
  `InsufficientPrivilegeError`s, confirming this role can find candidates but never
  mutate them.
- **Cannot** read even `id` from `customers`, `users`, `automations`, `invoices`, or
  `organizations` — no grant exists on any of those tables for this role at all, so
  every attempt fails at the database level, confirming the role's reach is exactly the
  one table/five columns it was given, nothing implicit or inherited.

**Not done this round (explicitly, to avoid overclaiming "end-to-end"):** the real,
running `EventBus.reconcile_stuck_events()` code in `backend/app/events/bus.py` was
**not** modified — it still executes its original `select(Event)` (full row) query via
the ordinary session factory today. This round proves the database-level mechanism
(role + grants + real permission enforcement) works correctly and matches exactly what
that method's narrowed query *would* need; wiring the live application code to actually
open a `klaros_discovery`-bound session for this one call site (new engine/session
plumbing, config wiring, careful separation from the main connection pool) is a
distinct, additional change with its own risk profile and is deliberately left for a
future round rather than rushed into a live event-processing code path this round.

## 11c. System Discovery Role — Extended to All 5 Paths' Grants (Round 5)

**Done this round:** `backend/scripts/db/provision_discovery_role.py`'s
`_TABLE_COLUMN_GRANTS` dict extended from 1 table (`events`) to 7 tables, covering all 5
real discovery paths:
- `automations(id, tenant_id, status, published_version_id)`,
  `automation_versions(id, trigger_type, trigger_config, condition, steps)` — Automation
  discovery.
- `agents(id, tenant_id, status, current_version_id)`,
  `agent_versions(id, triggers, instructions_snapshot)` — Agent discovery.
- `organizations(id, timezone, morning_brief_enabled, morning_brief_local_time,
  morning_brief_timezone)` — Morning Brief discovery.
- `agent_executions(id)` — Agent Recovery discovery (already this narrow in real
  application code).

Column lists match exactly what §10/§11 already determined real downstream code
consumes — no new field-inventory work was needed this round, only turning the
already-decided lists into actual grants.

**Real design gap found and fixed while implementing this (not present in Round 4's
`events`-only version, because `events` has no RLS enabled at all):** granting
`klaros_discovery` column-level SELECT on a table that separately has REAL enforcing RLS
(true for `automations` already, and for `agents`/`agent_versions`/`agent_executions` as
of this round's own `0054` migration, landed in the same round — see §17c) is not enough
by itself. `klaros_discovery`'s session never calls `set_tenant_context` (that is the
entire point of a cross-tenant discovery identity), so `current_tenant_id()` evaluates to
NULL for it, and the ordinary `tenant_select` policy (`USING (tenant_id =
current_tenant_id())`) would exclude every row for that role — the grant would be
real but silently useless, not a security hole, but a correctness bug that would have
been very easy to ship undetected (no error, just zero rows back, which a real discovery
sweep could misread as "nothing to do" rather than "the query is broken").

**Fix:** `provision_discovery_role.py` now also does, for every table in
`_TABLE_COLUMN_GRANTS`:
```sql
DROP POLICY IF EXISTS discovery_select ON <table>;
CREATE POLICY discovery_select ON <table> FOR SELECT TO klaros_discovery USING (true);
```
This is a second **permissive** policy scoped `TO klaros_discovery` only — PostgreSQL ORs
multiple applicable permissive policies together, so `klaros_discovery`'s queries are
governed by `tenant_select OR discovery_select`, i.e. "see nothing (no tenant context) OR
see everything (this named role only)" → sees everything, while `klaros_app` (which
`discovery_select` does not name) is governed by `tenant_select` alone, completely
unaffected. On a table with RLS disabled entirely (`organizations`, `events` today), the
policy object exists but has no effect (policies are inert until `ENABLE ROW LEVEL
SECURITY` is set) — harmless, and automatically becomes live the moment/if that table
ever gets RLS enabled in a future round, with no coordination step required.

**Why this lives in the Python provisioning script, not an Alembic migration:** a
migration containing `CREATE POLICY ... TO klaros_discovery` would fail outright if it
ever runs before the `klaros_discovery` role has been provisioned — and migrations and
role provisioning have no guaranteed ordering relative to each other (this is explicitly
documented as intentional flexibility in `provision_app_role.py`'s own docstring for
`klaros_app`: "Safe to run before OR after `alembic upgrade head`"). Keeping the
discovery-role carve-out policy inside the idempotent, rerun-safe provisioning script
(guarded by `DROP POLICY IF EXISTS` first, exactly like the migrations' own pattern)
sidesteps that ordering hazard entirely: the script only ever runs after it has already
created the role it references.

**Validated against real Postgres** (fresh Alembic-migrated `klaros_round5` database,
both roles provisioned via the real scripts — 29/29 checks, see §17c below for the full
suite description; the discovery-specific subset):
- Cross-tenant read succeeds on all 4 newly-granted RLS-enabled tables
  (`automations`, `agents`, `agent_versions`, `agent_executions`) — confirming the new
  `discovery_select` policy actually works, not just that the grant exists.
- Cross-tenant read succeeds on `organizations` (no RLS, so this was already expected to
  work, but re-confirmed after the script change).
- Still cannot read an ungranted column (`agents.name`) or do `SELECT *`
  (`automation_versions`) — column-level privilege enforcement unaffected by the new
  row-level policy.
- Still cannot UPDATE/DELETE/INSERT anywhere, including the newly-granted tables.
- Still cannot read a table with no grant at all (`payments`, spot-checked again this
  round to confirm the role's reach is still exactly its allow-list, nothing implicit).

**Not done this round (unchanged from Round 4, explicitly still deferred):** none of the
5 real call sites (`automation_service.py`, `agent_trigger_service.py`,
`morning_brief_service.py`, `agent_recovery_service.py`, `bus.py`) were rewired to
actually open a `klaros_discovery`-bound session. All 5 still run their original
full-ORM-row queries via the ordinary session factory today. This round proves the
database-level mechanism is now complete and correct for all 5 paths; the application-code
rewiring (new engine/session plumbing) remains a distinct, deliberately separate future
round's work per §34 item 2.

## 12-17. Migrations / Policies / USING / WITH CHECK / NULL Behavior / Index Evidence

**Real Alembic migrations written and validated this round** — see §17b's update below
for the migration content, and §19-29 for the full upgrade/downgrade/upgrade cycle
proof. Index evidence unchanged from Round 2 (every tenant_id column already indexed;
`webhook_events.tenant_id` is the one nullable case, and its NULL-context behavior is
now proven against the real migrated schema, not just raw SQL — see §17b).
`klaros_app`'s restricted role attributes were re-verified live again this round —
genuinely `NOSUPERUSER`/`NOBYPASSRLS`/`NOCREATEDB`/`NOCREATEROLE`, confirmed by direct
`pg_roles` query against the migrated database, not assumed.

## 17b. Tenant-Scoped RLS Policy Pattern — Designed and Validated (5-table proof, real Postgres)

**Pattern (proposed for the full 100-table rollout, validated on a 5-table subset this
round, NOT yet migrated anywhere):**

```sql
-- One reusable, fail-closed tenant-context accessor (created once, referenced by every
-- tenant table's policies — single source of truth instead of repeating the cast/NULLIF
-- logic inline in ~100 separate policy definitions).
CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS uuid AS $$
BEGIN
  RETURN NULLIF(current_setting('app.tenant_id', true), '')::uuid;
EXCEPTION WHEN invalid_text_representation THEN
  RETURN NULL;
END;
$$ LANGUAGE plpgsql STABLE;

-- Per tenant-owned table (4 separate policies, not one FOR ALL, for command-level clarity):
ALTER TABLE <table> ENABLE ROW LEVEL SECURITY;  -- NOT FORCE — unchanged, out of scope
CREATE POLICY tenant_select ON <table> FOR SELECT
  USING (tenant_id = current_tenant_id());
CREATE POLICY tenant_insert ON <table> FOR INSERT
  WITH CHECK (tenant_id = current_tenant_id());
CREATE POLICY tenant_update ON <table> FOR UPDATE
  USING (tenant_id = current_tenant_id())
  WITH CHECK (tenant_id = current_tenant_id());
CREATE POLICY tenant_delete ON <table> FOR DELETE
  USING (tenant_id = current_tenant_id());
```

**Why this NULL-safety shape:** `current_setting('app.tenant_id', true)` returns NULL
when unset (the `true` "missing_ok" arg avoids an error); `NULLIF(..., '')` collapses an
empty string to NULL too; casting NULL to `::uuid` yields NULL (not an error); and a
genuinely malformed non-UUID string is caught by the `EXCEPTION WHEN
invalid_text_representation` clause and also mapped to NULL. In every one of those three
cases, `tenant_id = NULL` evaluates to `NULL` (SQL three-valued logic), which `WHERE`/
`USING` treats as "exclude this row" — so **no context, empty context, and malformed
context all fail closed to zero visible/writable tenant rows, with no exception ever
reaching the caller**, exactly the brief's required semantics.

**Validation methodology:** applied directly (raw SQL, not yet as an Alembic migration)
to 5 representative tables on the real, disposable, Alembic-migrated `klaros_rls_probe2`
Postgres instance, chosen to span the classifications from §4-9:
- `customers` — an ordinary simple TENANT_SCOPED table, no RLS previously.
- `webhook_events` — the one table with a **nullable** `tenant_id`.
- `automations` — TENANT_SCOPED **and** touched by a cross-tenant discovery sweep.
- `users` — already had a Phase-0 **permissive audit-mode** policy
  (`USING(true)/WITH CHECK(true)`); this table proves the pattern also works as an
  in-place *upgrade* from audit-mode to real enforcement (`DROP POLICY
  tenant_isolation_audit_policy` + `CREATE POLICY` as above), not just on a bare table.
- `invoices` — financial, FK-heavy (references `customers`).

All queries ran through the **actual restricted `klaros_app` role** (provisioned via the
real `backend/scripts/db/provision_app_role.py` script against the probe database, then
independently re-verified live via `pg_roles` — genuinely `rolsuper=false`,
`rolbypassrls=false`, `rolcreatedb=false`, `rolcreaterole=false`), never as the
Postgres superuser/table-owner connection — per the brief's explicit requirement that
RLS be proven under the restricted runtime role, not the owner.

**Results: 43/43 checks passed.**
- `customers` (25 checks): SELECT isolation both directions; NULL context, empty-string
  context, and malformed (`'not-a-uuid-at-all'`) context each → zero rows with **no
  exception**; `WITH CHECK` blocks a cross-tenant INSERT (`InsufficientPrivilegeError`)
  and blocks reassigning an existing row's `tenant_id` to another tenant, while a
  same-tenant INSERT/UPDATE succeeds normally; a no-context session cannot INSERT at
  all; UPDATE/DELETE of another tenant's row affects exactly 0 rows (not an error — a
  real Postgres RLS property, since the row is simply invisible to the `WHERE`/`USING`
  clause); connection-pool reuse across tenant A → B → A sequential acquisitions on a
  **single physical connection** (`min_size=1,max_size=1`) shows no cross-tenant leakage,
  and a fresh transaction on that same reused connection with no `SET LOCAL` at all sees
  zero rows (no stale context survives a transaction boundary); an explicit `SET LOCAL`
  + `ROLLBACK` + re-acquire of the same connection also shows no stale context; 4
  concurrent transactions (2 tenant A, 2 tenant B, interleaved via `asyncio.gather` with
  an injected delay to widen the race window) stay correctly isolated.
- `webhook_events` (2 checks, plus reuses the NULL-context checks above): a
  tenant-context session cannot insert a NULL-`tenant_id` row (blocked by `WITH CHECK`,
  since `NULL = current_tenant_id()` is never true even when `current_tenant_id()`
  itself is a real, non-null tenant UUID); a NULL-`tenant_id` row inserted by the
  unrestricted owner connection (simulating pre-tenant-resolution webhook ingestion) is
  **invisible under every tenant context** — confirming the fail-closed design decision
  documented inline in the setup SQL that webhook resolution will need its own separate
  system identity in a later round, not an app-level tenant session.
- `automations`, `users`, `invoices` (6 checks each, 18 total): SELECT isolation both
  directions, no-context → zero rows, UPDATE/DELETE of another tenant's row → 0 rows
  affected, and (via the unrestricted owner connection) explicit confirmation that the
  other tenant's row still physically exists afterward — i.e. the blocked DELETE was a
  real no-op, not a silent, unreported success.

One real test-script bug was hit and fixed along the way (not an RLS defect): an early
pass compared pool-reuse/concurrency results against a hardcoded expected row set that
didn't account for an extra row created by an earlier INSERT test in the same run;
fixed by deriving the expected set live from the unrestricted owner connection
immediately before each check, and all 43 checks then passed cleanly on the corrected
script, confirmed by an independent full rerun.

**Round 4 update — the pattern is now real, committed-to-working-tree migrations,
re-validated identically:**

- `backend/alembic/versions/0052_rls_tenant_context_function.py` — creates
  `current_tenant_id()` exactly as designed above (`down_revision = 0051`). Downgrade
  drops the function.
- `backend/alembic/versions/0053_rls_tier1_real_enforcement.py` (`down_revision = 0052`)
  — applies the 4 real policies to all 5 proven tables: `customers`/`webhook_events`/
  `automations`/`invoices` get `ENABLE ROW LEVEL SECURITY` + the 4 new policies (they had
  no RLS at all before); `users` instead `DROP POLICY tenant_isolation_audit_policy` (its
  pre-existing Phase-0 permissive policy) then adds the same 4 real policies — the
  in-place conversion proven safe in the original §17b raw-SQL pass. Downgrade is exactly
  symmetric: the 4 newly-enforced tables get their real policies dropped and RLS
  disabled entirely (returning them to their pre-migration state); `users` gets its real
  policies dropped and the original audit-mode `USING(true)/WITH CHECK(true)` policy
  recreated — restoring the exact pre-migration state, not an approximation.

**Migration-cycle validation (fresh database, real `alembic upgrade head` from empty,
never touched by `conftest.py`):**
- `alembic upgrade head` (base → 0053): exit 0, no errors. Verified via direct
  `pg_policies` query: all 5 tables have exactly 4 policies each (`tenant_select`/
  `tenant_insert`/`tenant_update`/`tenant_delete`), correct `USING`/`WITH CHECK` SQL text,
  and `current_tenant_id()` exists in `pg_proc`.
- `alembic downgrade 0051` (0053 → 0051, back past both new migrations): exit 0, no
  errors. Verified: `users` has exactly its original `tenant_isolation_audit_policy`
  back (nothing else); the other 4 tables have `relrowsecurity = false` again (RLS fully
  off, matching their pre-migration state); `current_tenant_id()` no longer exists in
  `pg_proc`.
- `alembic upgrade head` again (0051 → 0053): exit 0, no errors, back to head with the
  real policies restored.

**Re-ran the exact same 43-check validation suite from the raw-SQL pass, unmodified
except pointing at the real migrated database instead of the probe instance with
manually-applied SQL — identical result, 43/43 passed** (25/25 on `customers`/
`webhook_events`'s NULL-context and pool/concurrency/rollback checks, 18/18 on
`automations`/`users`/`invoices`'s isolation checks) — confirming the migration produces
behavior identical to what was hand-verified in Round 3, not merely "the SQL ran without
error."

**A real, unplanned finding while doing this:** running the ordinary pytest suite
(`tests/test_postgres_rls_audit_mode.py`, `tests/test_restricted_app_role_cutover.py`)
against this same migrated database, expecting it to be a harmless sanity check,
**silently destroyed the migration's RLS state** — `conftest.py`'s autouse
`_reset_database` fixture ran `Base.metadata.drop_all()` + `Base.metadata.create_all()`
before each test, which drops and recreates every table via SQLAlchemy's declarative
definitions (no RLS-authoring DDL exists there), wiping all 5 tables' real policies while
leaving `alembic_version` falsely stamped at `0053` — confirmed via direct `pg_policies`
query afterward (empty) despite `alembic_version` still reading `0053`. This is the
**third independent occurrence** of the exact same root cause first found in Round 2
(RLS state) and again in Round 3 (the "0048 downgrade bug" false alarm): **any database
this phase wants to keep in a genuinely migration-verified state must never be reused
for an ordinary pytest run**, since the existing test suite's schema-provisioning
fixture is fundamentally incompatible with verifying migration-authored DDL. All 43+15
checks reported in this log were captured on databases before any pytest run touched
them; a fresh database (`klaros_discovery_test`) was used for the discovery-role work in
§11b specifically to avoid this contamination. This is now recorded as a hard
requirement for the future round that designs the full RLS test matrix (§34).

**Scaling to the other ~95 TENANT_SCOPED tables and deciding `FORCE ROW LEVEL SECURITY`
(still explicitly out of scope for this phase) remain next-round work**, per the
coordinator's explicit instruction not to rush past this smaller batch.

## 17c. Second Batch — `0054` (Round 5, 10 tables)

**Migration:** `backend/alembic/versions/0054_rls_tier2_real_enforcement.py`
(`down_revision = 0053`). Same exact 4-policy pattern as `0053`, applied to:
- **5 already-audit-mode tables, converted in place** (drop
  `tenant_isolation_audit_policy`, add the 4 real policies — the `users`-proven pattern
  from `0053`): `agents`, `agent_versions`, `agent_tool_permissions`, `agent_executions`,
  `agent_execution_steps`. All 5 originally got audit-mode RLS from migrations
  `0045`/`0046`.
- **5 tables with no RLS at all before this migration** (`ENABLE ROW LEVEL SECURITY` +
  the 4 real policies, the `customers`-proven pattern): `automation_versions`,
  `automation_executions`, `automation_execution_steps`, `invoice_line_items`,
  `payments`.

**Batch selection rationale:** not arbitrary — chosen to directly overlap with this
round's discovery-role grant work (§11c) so the new `discovery_select` policy mechanism
would be exercised against a real enforcing-RLS table in the same round it was designed,
rather than staying theoretical. `agents`/`agent_versions`/`agent_executions` are 3 of the
4 newly-granted discovery tables; `automation_versions` is the other table Automation
discovery needs (`automations` itself already got real RLS in `0053`). The remaining 4
tables (`agent_tool_permissions`, `agent_execution_steps`, `automation_executions`,
`automation_execution_steps`, `invoice_line_items`, `payments` — the non-discovery members
of the batch) round out coherent domain families (the rest of the agent-runtime and
automation-execution tables) plus continue the plain-table rollout with two ordinary
financial tables, matching the coordinator's "mix of plain TENANT_SCOPED tables and
already-audit-mode tables" instruction.

**Every one of the 10 tables' `tenant_id` was confirmed** (live query against the
freshly-migrated database, before writing the migration) to be indexed and `NOT NULL` —
none is a second `webhook_events`-style nullable-tenant_id special case.

**Migration-cycle validation (fresh database, real `alembic upgrade head`, never touched
by `conftest.py` or any pytest run):**
- `alembic upgrade head` (base → 0054): exit 0, no errors, both on the database used for
  the isolation test suite below and, separately, re-verified on a brand-new
  never-before-touched database (`klaros_round5_fresh`) to rule out any state leaking from
  the first run.
- Direct `pg_policies`/`pg_class` query confirmed all 10 tables have exactly the 4
  expected policies (`tenant_select`/`tenant_insert`/`tenant_update`/`tenant_delete`) with
  `relrowsecurity = true`.
- `alembic downgrade 0053` (0054 → 0053): exit 0, no errors. Verified: the 5 audit-mode
  tables have exactly their original `tenant_isolation_audit_policy` back (nothing else);
  the 5 newly-enforced tables have `relrowsecurity = false` again — both groups restored
  to their exact pre-`0054` state, not an approximation.
- `alembic upgrade head` again (0053 → 0054): exit 0, no errors, back to head with the
  real policies restored.

**Real-Postgres spot-check suite — 29/29 passed** (not the full 43-check treatment on
every table of the batch, per the coordinator's explicit "spot-check... not necessarily
full suite every table" instruction; a 4-table sample spanning both halves of the batch):
- `agents`, `agent_executions` (audit-mode → real, both also discovery-grant tables),
  `automation_versions` (new RLS, also a discovery-grant table), `payments` (new RLS,
  plain financial table, no discovery involvement — included specifically to prove the
  pattern isn't only validated on discovery-adjacent tables).
- Each: SELECT isolation both directions (tenant A sees only its own row, tenant B sees
  only its own row), no-context session → zero rows (fail-closed, no exception), and a
  cross-tenant UPDATE attempt affecting exactly 0 rows (confirmed via the owner connection
  that the target row still exists afterward — a real no-op, not a silently-swallowed
  failure).
- Plus all 13 of §11c's discovery-grant checks (cross-tenant reads succeed on the 4
  newly-granted RLS-enabled tables and on `organizations`; ungranted columns/tables and
  all DML remain refused) — run in the same suite, against the same database, in the same
  round, specifically so the discovery-role fix in §11c would be proven against a table
  that actually has RLS enabled, not just asserted in the abstract.

One real methodology fix needed along the way (not an RLS defect): the first pass of this
suite issued `SELECT set_config('app.tenant_id', ..., true)` and later statements on the
same `asyncpg` connection outside any explicit transaction — `set_config(..., true)` is
`SET LOCAL` semantics, scoped to the current transaction only, and `asyncpg` does not
open an implicit transaction the way a SQLAlchemy `AsyncSession` does (which is what
`app/db/session.py`'s real `set_tenant_context` always runs inside) — so the context was
silently reverting to unset between statements, and an early version of the suite
misread this as an isolation failure. Fixed by wrapping each check in an explicit
`async with conn.transaction():` block, matching the real application's actual call
contract; re-ran cleanly afterward. This is a test-harness-realism finding (already
implicitly understood from the `43-check` suite's own pool/transaction sections in §17b),
not a new discovery about the RLS policies themselves.

## 17d. Third Batch — `0055` (Round 6, 12 tables) + Round 5's Leftover 6 Tables

**Migration:** `backend/alembic/versions/0055_rls_tier3_real_enforcement.py`
(`down_revision = 0054`). Same exact 4-policy pattern as `0053`/`0054`, applied to:
- **6 already-audit-mode tables, converted in place**: `approval_requests`, `audit_logs`,
  `company_memories`, `integration_connections` — completing the original Phase-0 "tier 1"
  five from migration `0040` (`users` was the fifth, already converted in `0053`) —
  plus `recommendation_runs`, `recommendations` (originally audit-mode via `0044`).
- **6 tables with no RLS at all before this migration**: `leads`, `quotes`,
  `quote_line_items`, `jobs`, `notifications`, `contracts` — deliberately spread across
  CRM/sales/ops/comms rather than clustering in one domain, continuing the plain-table
  rollout.

All 12 confirmed via a live query (before writing the migration) to have `tenant_id`
indexed and `NOT NULL` — no new nullable-tenant_id special case.

**Migration-cycle validation:**
- `alembic upgrade head` (0054 → 0055) on the long-lived migrated database used for
  isolation testing: exit 0, no errors. `pg_policies`/`pg_class` query confirmed all 12
  tables have exactly the 4 expected real policies, `relrowsecurity = true`.
- `alembic downgrade 0054` (0055 → 0054): exit 0, no errors. Verified: the 6 audit-mode
  tables have exactly their original `tenant_isolation_audit_policy` back; the 6
  newly-enforced tables have `relrowsecurity = false` again — exact pre-migration state
  restored, not an approximation.
- `alembic upgrade head` again (0054 → 0055): exit 0, no errors, back to head.
- Separately, a clean `base → head` run (all 55 migrations) on a brand-new,
  never-before-touched database (`klaros_round6_fresh`): exit 0, no errors.

**Real-Postgres check suite — 40/40 passed**, combining two things in one run per the
coordinator's instruction to fold Round 5's leftover verification into this round's
regular pass:
- **4-table spot-check sample from this round's own batch** (`audit_logs`, `leads`,
  `contracts`, `approval_requests`): SELECT isolation both directions, no-context session
  → zero rows (fail-closed, no exception), cross-tenant UPDATE attempt affecting exactly
  0 rows.
- **All 6 of Round 5's previously-only-structurally-verified tables, now individually
  isolation-proven** (`agent_versions`, `agent_tool_permissions`, `agent_execution_steps`,
  `automation_executions`, `automation_execution_steps`, `invoice_line_items`) — same
  4-check treatment each (SELECT isolation both directions, fail-closed NULL context,
  blocked cross-tenant UPDATE as a genuine 0-row no-op). Building these rows required
  chaining through each table's real FK dependencies (`agents` → `agent_versions` →
  `agent_executions` → `agent_execution_steps`; `automations` → `automation_versions` →
  `automation_executions` → `automation_execution_steps`), all constructed fresh in this
  round's own test run rather than assumed from Round 5's now-gone data.
- No new test-harness pitfalls found this round — the explicit-transaction fix from
  §17c's Round 5 finding was applied from the start this time.

**Running total across all three migrations (`0053`+`0054`+`0055`, 27 tables):** all 27
have structural (`pg_policies`/`relrowsecurity`) verification via the migration-cycle
checks. Of those, 19 now also have an individual real-Postgres isolation proof (SELECT
both directions, fail-closed, blocked-UPDATE-no-op): the 5 from `0053`'s original 43-check
suite (`customers`, `webhook_events`, `automations`, `users`, `invoices`), 4 from `0054`'s
spot-check (`agents`, `agent_executions`, `automation_versions`, `payments`), the 6
Round-5 leftovers proven this round (`agent_versions`, `agent_tool_permissions`,
`agent_execution_steps`, `automation_executions`, `automation_execution_steps`,
`invoice_line_items`), and 4 from this round's own spot-check (`audit_logs`, `leads`,
`contracts`, `approval_requests`). The remaining 8 have structural verification only —
`company_memories`, `integration_connections`, `recommendation_runs`, `recommendations`,
`quotes`, `quote_line_items`, `jobs`, `notifications` — tracked in §34 for a future
round's full test matrix.

## 17e. Fourth Batch — `0056` (Round 7, 12 tables) + Round 6's Leftover 8 Tables

**Migration:** `backend/alembic/versions/0056_rls_tier4_real_enforcement.py`
(`down_revision = 0055`). Same exact 4-policy pattern as `0053`-`0055`, applied to:
- **6 already-audit-mode tables, converted in place** (originally `0043`/`0051`): the
  full Business Discovery → Blueprint → Journey pipeline —
  `discovery_sessions`, `discovery_turns`, `business_blueprints`, `blueprint_sections`,
  `blueprint_claims`, `business_journeys`.
- **6 tables with no RLS at all before this migration**, spread across ops/marketing/
  comms rather than one domain: `appointments`, `call_sessions`, `campaigns`,
  `campaign_leads`, `notification_preferences`, `workers`.

All 12 confirmed via a live query (before writing the migration) to have `tenant_id`
indexed and `NOT NULL`.

**Migration-cycle validation:**
- `alembic upgrade head` (0055 → 0056) on the long-lived migrated database: exit 0, no
  errors. `pg_policies`/`pg_class` confirmed all 12 tables have exactly the 4 expected
  real policies, `relrowsecurity = true`.
- `alembic downgrade 0055` (0056 → 0055): exit 0, no errors. Verified: the 6 audit-mode
  tables have exactly their original `tenant_isolation_audit_policy` back; the 6
  newly-enforced tables have `relrowsecurity = false` again — exact pre-migration state
  restored.
- `alembic upgrade head` again (0055 → 0056): exit 0, no errors, back to head.
- Separately, a clean `base → head` run (all 56 migrations) on a brand-new,
  never-before-touched database (`klaros_round7_fresh`): exit 0, no errors.

**Real-Postgres check suite — 48/48 passed**, combining this round's own spot-check with
a full backfill of Round 6's verification debt, exactly as the coordinator asked:
- **4-table spot-check sample from this round's own batch** (`discovery_sessions`,
  `campaigns`, `notification_preferences`, `workers`): SELECT isolation both directions,
  fail-closed NULL context, blocked cross-tenant UPDATE as a genuine 0-row no-op.
- **All 8 of Round 6's previously-only-structurally-verified tables, now individually
  isolation-proven** (`company_memories`, `integration_connections`,
  `recommendation_runs`, `recommendations`, `quotes`, `quote_line_items`, `jobs`,
  `notifications`) — same 4-check treatment each. Building `recommendation_runs`/
  `recommendations` rows required chaining through their real FK dependencies
  (`business_blueprints` → `recommendation_runs` → `recommendations`) and satisfying
  several NOT NULL JSON/text columns the ORM defaults normally fill in but raw SQL does
  not (`what`, `why`, `based_on`, `dependencies`, `alternatives`, `confidence`, `source`)
  — a minor test-construction detail, not an RLS finding.

**Running total across all four migrations (`0053`-`0056`, 39 tables):** all 39 have
structural verification; 31 of those now also have an individual real-Postgres isolation
proof (the 5 from `0053`'s 43-check suite, 4 from `0054`'s spot-check + all 6 of its
leftovers now proven, 4 from `0055`'s spot-check + all 8 of its leftovers now proven, and
4 from `0056`'s own spot-check this round). The remaining 8 — this round's own
non-spot-checked half of the batch (`discovery_turns`, `business_blueprints`,
`blueprint_sections`, `blueprint_claims`, `business_journeys`, `appointments`,
`call_sessions`, `campaign_leads`) — have structural verification only, tracked in §34
for a future round's regular backfill, continuing the same discipline established in
Rounds 6 and 7.

## 17f. Fifth Batch — `0057` (Round 8, 13 tables) + Round 7's Leftover 8 Tables

**Migration:** `backend/alembic/versions/0057_rls_tier5_real_enforcement.py`
(`down_revision = 0056`). Same exact 4-policy pattern as `0053`-`0056`, applied to:
- **7 already-audit-mode tables, converted in place** (originally `0041`/`0048`/`0050`):
  the MCP server pair (`mcp_tool_exposures`, `mcp_client_credentials`), the full Website
  Builder family (`websites`, `website_versions`, `website_pages`, `website_sections`),
  and the vertical extension registry join table (`organization_vertical_extensions`).
  **This completes every remaining already-audit-mode table except the 7 Medical Tourism
  tables** (`0049`), deliberately left as a single coherent domain family for the next
  round rather than split across two migrations.
- **6 tables with no RLS at all before this migration**, all from the finance domain —
  a natural pairing with `invoices`/`payments`/`invoice_line_items` already converted in
  `0053`/`0054`, and with the CashForecast metadata-leak fix from an earlier round:
  `cash_forecasts`, `cash_forecast_items`, `payouts`, `payment_allocations`,
  `credit_notes`, `credit_note_line_items`.

All 13 confirmed via a live query (before writing the migration) to have `tenant_id`
indexed and `NOT NULL`.

**Migration-cycle validation:**
- `alembic upgrade head` (0056 → 0057) on the long-lived migrated database: exit 0, no
  errors. `pg_policies`/`pg_class` confirmed all 13 tables have exactly the 4 expected
  real policies, `relrowsecurity = true`.
- `alembic downgrade 0056` (0057 → 0056): exit 0, no errors. Verified: the 7 audit-mode
  tables have exactly their original `tenant_isolation_audit_policy` back; the 6
  newly-enforced tables have `relrowsecurity = false` again — exact pre-migration state
  restored.
- `alembic upgrade head` again (0056 → 0057): exit 0, no errors, back to head.
- Separately, a clean `base → head` run (all 57 migrations) on a brand-new,
  never-before-touched database (`klaros_round8_fresh`): exit 0, no errors.

**Real-Postgres check suite — 48/48 passed**, combining this round's own spot-check with
a full backfill of Round 7's verification debt:
- **4-table spot-check sample from this round's own batch** (`websites`,
  `cash_forecasts`, `payouts`, `mcp_tool_exposures`): SELECT isolation both directions,
  fail-closed NULL context, blocked cross-tenant UPDATE as a genuine 0-row no-op.
- **All 8 of Round 7's previously-only-structurally-verified tables, now individually
  isolation-proven** (`discovery_turns`, `business_blueprints`, `blueprint_sections`,
  `blueprint_claims`, `business_journeys`, `appointments`, `call_sessions`,
  `campaign_leads`) — same 4-check treatment each. `discovery_turns`/`blueprint_sections`/
  `blueprint_claims` required chaining through their real FK dependencies
  (`discovery_sessions`/`business_blueprints`); `call_sessions` needed an explicit
  `engine_state` value (`{}`) that raw SQL doesn't get from the ORM's own column default —
  a minor test-construction detail, not an RLS finding.

**Running total across all five migrations (`0053`-`0057`, 52 tables):** all 52 have
structural verification; 43 of those now also have an individual real-Postgres isolation
proof (Round 7's running total of 31, plus all 8 of `0056`'s leftovers proven this round,
plus 4 from `0057`'s own spot-check this round). The remaining 9 — this round's own
non-spot-checked remainder of the batch (`mcp_client_credentials`,
`organization_vertical_extensions`, `website_versions`, `website_pages`,
`website_sections`, `cash_forecast_items`, `payment_allocations`, `credit_notes`,
`credit_note_line_items`) — have structural verification only, tracked in §34 for a
future round's regular backfill, continuing the same discipline established in Rounds 6,
7, and 8.

## 17g. Sixth Batch — `0058` (Round 9, 13 tables) — All 7 Medical Tourism Tables + Round 8's Leftover 9 Tables

**Migration:** `backend/alembic/versions/0058_rls_tier6_medical_tourism_and_jobs.py`
(`down_revision = 0057`). Same exact 4-policy pattern as `0053`-`0057`, applied to:
- **All 7 Medical Tourism tables** (originally `0049`), converted in place from
  audit-mode to real enforcement: `medical_tourism_providers`,
  `medical_tourism_provider_credentials`, `medical_tourism_procedures`,
  `medical_tourism_provider_procedures`, `medical_tourism_patient_leads`,
  `medical_tourism_consultations`, `medical_tourism_referral_commissions`. **This is the
  last remaining already-audit-mode cohort — after this migration, zero tables remain in
  Phase-0 permissive audit-mode anywhere in the schema.**
- **6 tables with no RLS at all before this migration**, from the Jobs and Finance
  domains — a natural pairing with `jobs`/`invoices`/`payments`/`refunds`'s siblings
  already converted in earlier rounds: `job_costs`, `job_materials`, `job_tasks`,
  `job_qa`, `job_attachments`, `refunds`.

All 13 confirmed via a live query (before writing the migration) to have `tenant_id`
indexed and `NOT NULL`.

**Medical Tourism given full rigor, not fast-tracked.** The coordinator explicitly
flagged that Medical Tourism is the vertical this entire security track has been
building toward (the first real, shipped vertical extension on the platform per the
original brief) and asked for the same careful treatment as every other batch rather
than a rushed pass just because it happens to land as one coherent domain. Concretely:
this round's real-Postgres spot-check sample deliberately includes 4 of the 7 Medical
Tourism tables (`medical_tourism_providers`, `medical_tourism_procedures`,
`medical_tourism_patient_leads`, `medical_tourism_referral_commissions`) rather than the
usual "roughly 1/3 of the batch" ratio the other rounds used — Medical Tourism is
over-represented in the sample on purpose. `medical_tourism_referral_commissions` in
particular required building a real FK chain through a `referrals` row (the retention
domain's own table, itself not yet RLS-enabled but that doesn't block a valid FK target)
to prove the pattern holds even for the most cross-domain-linked table in the vertical —
not just the simplest standalone ones (`Provider`, `Procedure`).

**Migration-cycle validation:**
- `alembic upgrade head` (0057 → 0058) on the long-lived migrated database: exit 0, no
  errors. `pg_policies`/`pg_class` confirmed all 13 tables have exactly the 4 expected
  real policies, `relrowsecurity = true`.
- `alembic downgrade 0057` (0058 → 0057): exit 0, no errors. Verified: the 7 Medical
  Tourism tables have exactly their original `tenant_isolation_audit_policy` back; the 6
  newly-enforced tables have `relrowsecurity = false` again — exact pre-migration state
  restored.
- `alembic upgrade head` again (0057 → 0058): exit 0, no errors, back to head.
- Separately, a clean `base → head` run (all 58 migrations) on a brand-new,
  never-before-touched database (`klaros_round9_fresh`): exit 0, no errors.

**Real-Postgres check suite — 60/60 passed**, combining this round's own (Medical-
Tourism-weighted) spot-check with a full backfill of Round 8's verification debt:
- **6-table spot-check sample from this round's own batch**: `medical_tourism_providers`,
  `medical_tourism_procedures`, `medical_tourism_patient_leads`,
  `medical_tourism_referral_commissions` (4 Medical Tourism tables — see above), plus
  `job_costs` and `refunds`. SELECT isolation both directions, fail-closed NULL context,
  blocked cross-tenant UPDATE as a genuine 0-row no-op, for every one.
- **All 9 of Round 8's previously-only-structurally-verified tables, now individually
  isolation-proven** (`mcp_client_credentials`, `organization_vertical_extensions`,
  `website_versions`, `website_pages`, `website_sections`, `cash_forecast_items`,
  `payment_allocations`, `credit_notes`, `credit_note_line_items`) — same 4-check
  treatment each. `website_versions`/`website_pages`/`website_sections` required chaining
  through their real FK dependencies (`websites` → `website_versions` → `website_pages`
  → `website_sections`), each needing an explicit JSON default (`theme`/`navigation`/
  `seo_defaults`/`generation_provenance` on `website_versions`, `seo` on `website_pages`,
  `props` on `website_sections`) that raw SQL doesn't get from the ORM's own column
  default — the same category of minor test-construction detail as Round 8's
  `call_sessions.engine_state` finding, not an RLS finding. `organization_vertical_extensions`
  required a real `vertical_extensions` row (a GLOBAL_SHARED reference table, no
  `tenant_id`) as its FK target, and `mcp_client_credentials.token_hash` is a
  platform-wide unique column (not tenant-scoped), so the test generates a fresh value
  per run rather than a fixed literal to stay idempotent across repeated executions.

**Running total across all six migrations (`0053`-`0058`, 65 tables):** all 65 have
structural verification; 58 of those now also have an individual real-Postgres isolation
proof (Round 8's running total of 43, plus all 9 of `0057`'s leftovers proven this round,
plus 6 from `0058`'s own spot-check this round, weighted toward Medical Tourism as
described above). The remaining 7 — this round's own non-spot-checked remainder of the
batch (`medical_tourism_provider_credentials`, `medical_tourism_provider_procedures`,
`medical_tourism_consultations`, `job_materials`, `job_tasks`, `job_qa`,
`job_attachments`) — have structural verification only, tracked in §34 for a future
round's regular backfill, continuing the same discipline established in Rounds 6-9.

## 17h. Seventh Batch — `0059` (Round 10, 13 tables) + Round 9's Leftover 7 Tables

**Migration:** `backend/alembic/versions/0059_rls_tier7_real_enforcement.py`
(`down_revision = 0058`). First migration since `0040` that needs no audit-mode
conversion step at all — every table is a plain `ENABLE ROW LEVEL SECURITY` + 4-policy
addition, since Round 9's `0058` retired the last audit-mode cohort. Applied to 13 tables
spanning three domains, deliberately not clustered into one:
- Content/Marketing (7): `content_assets`, `content_performance`,
  `content_publications`, `content_variants`, `marketing_content`,
  `marketing_lead_sources`, `marketing_spend`.
- Morning Brief (3): `morning_briefs`, `morning_brief_insights`,
  `morning_brief_recommendations`.
- Customer (3): `customer_feedback`, `customer_notes`, `customer_signoffs`.

All 13 confirmed via a live query (before writing the migration) to have `tenant_id`
indexed and `NOT NULL`, and to carry no pre-existing policy at all (not even audit-mode —
consistent with §35's tally, which already showed the audit-mode category empty).

**Migration-cycle validation:**
- `alembic upgrade head` (0058 → 0059) on the long-lived migrated database: exit 0, no
  errors. `pg_policies`/`pg_class` confirmed all 13 tables have exactly the 4 expected
  real policies, `relrowsecurity = true`.
- `alembic downgrade 0058` (0059 → 0058): exit 0, no errors. Verified: all 13 tables have
  `relrowsecurity = false` again and zero policies — exact pre-migration state restored
  (simpler to verify than every prior round's downgrade, since there is no audit-mode
  policy to check for recreation on any table anymore).
- `alembic upgrade head` again (0058 → 0059): exit 0, no errors, back to head.
- Separately, a clean `base → head` run (all 59 migrations) on a brand-new,
  never-before-touched database (`klaros_round10_fresh`): exit 0, no errors.

**Real-Postgres check suite — 44/44 passed**, combining this round's own spot-check with
a full backfill of Round 9's verification debt:
- **4-table spot-check sample from this round's own batch** (`content_assets`,
  `marketing_spend`, `morning_briefs`, `customer_feedback`): SELECT isolation both
  directions, fail-closed NULL context, blocked cross-tenant UPDATE as a genuine 0-row
  no-op.
- **All 7 of Round 9's previously-only-structurally-verified tables, now individually
  isolation-proven** (`medical_tourism_provider_credentials`,
  `medical_tourism_provider_procedures`, `medical_tourism_consultations`,
  `job_materials`, `job_tasks`, `job_qa`, `job_attachments`) — same 4-check treatment
  each. The three Medical Tourism tables required building real FK chains through fresh
  `medical_tourism_providers`/`medical_tourism_procedures`/`appointments` rows (the
  Consultation extends Appointment, per the vertical's one-to-one-extension design), the
  same pattern Round 9 already proved for `medical_tourism_referral_commissions` — no new
  finding, just continued diligence.

**Running total across all seven migrations (`0053`-`0059`, 78 tables):** all 78 have
structural verification; 69 of those now also have an individual real-Postgres isolation
proof (Round 9's running total of 58, plus all 7 of `0058`'s leftovers proven this round,
plus 4 from `0059`'s own spot-check this round). The remaining 9 — this round's own
non-spot-checked remainder of the batch (`content_performance`, `content_publications`,
`content_variants`, `marketing_content`, `marketing_lead_sources`,
`morning_brief_insights`, `morning_brief_recommendations`, `customer_notes`,
`customer_signoffs`) — have structural verification only, tracked in §34 for a future
round's regular backfill, continuing the same discipline established in Rounds 6-10.

## 17i. Eighth Batch — `0060` (Round 11, 13 tables) + Round 10's Leftover 9 Tables

**Migration:** `backend/alembic/versions/0060_rls_tier8_real_enforcement.py`
(`down_revision = 0059`). All 13 tables newly enforced (no audit-mode cohort remains
anywhere), spanning two adjacent customer-lifecycle-marketing domains:
- Retention/Referral (8): `referral_codes`, `referral_programs`, `referral_rewards`,
  `referrals`, `retention_activities`, `retention_campaigns`, `retention_enrollments`,
  `retention_opportunities`.
- Outbound (5): `outbound_lists`, `outbound_contacts`, `outbound_sequences`,
  `outbound_steps`, `outbound_activities`.

All 13 confirmed via a live query (before writing the migration) to have `tenant_id`
indexed and `NOT NULL`, and to carry no pre-existing policy at all.

**Migration-cycle validation:**
- `alembic upgrade head` (0059 → 0060) on the long-lived migrated database: exit 0, no
  errors. `pg_policies`/`pg_class` confirmed all 13 tables have exactly the 4 expected
  real policies, `relrowsecurity = true`.
- `alembic downgrade 0059` (0060 → 0059): exit 0, no errors. Verified: all 13 tables have
  `relrowsecurity = false` again and zero policies — exact pre-migration state restored.
- `alembic upgrade head` again (0059 → 0060): exit 0, no errors, back to head.
- Separately, a clean `base → head` run (all 60 migrations) on a brand-new,
  never-before-touched database (`klaros_round11_fresh`): exit 0, no errors.

**Real-Postgres check suite — 52/52 passed**, combining this round's own spot-check with
a full backfill of Round 10's verification debt:
- **4-table spot-check sample from this round's own batch** (`referral_codes`,
  `retention_activities`, `outbound_lists`, `outbound_contacts`): SELECT isolation both
  directions, fail-closed NULL context, blocked cross-tenant UPDATE as a genuine 0-row
  no-op.
- **All 9 of Round 10's previously-only-structurally-verified tables, now individually
  isolation-proven** (`content_performance`, `content_publications`, `content_variants`,
  `marketing_content`, `marketing_lead_sources`, `morning_brief_insights`,
  `morning_brief_recommendations`, `customer_notes`, `customer_signoffs`) — same 4-check
  treatment each. The content family required chaining through its real FK dependencies
  (`marketing_content` → `content_variants` → `content_publications` →
  `content_performance`, each needing an explicit NOT NULL column raw SQL doesn't get
  from the ORM's own default — `recorded_at` on `content_performance` was this round's
  instance of the same minor test-construction detail Rounds 8-10 have each hit once).

**Running total across all eight migrations (`0053`-`0060`, 91 tables):** all 91 have
structural verification; 82 of those now also have an individual real-Postgres isolation
proof (Round 10's running total of 69, plus all 9 of `0059`'s leftovers proven this
round, plus 4 from `0060`'s own spot-check this round). The remaining 9 — this round's
own non-spot-checked remainder of the batch (`referral_programs`, `referral_rewards`,
`referrals`, `retention_campaigns`, `retention_enrollments`, `retention_opportunities`,
`outbound_sequences`, `outbound_steps`, `outbound_activities`) — have structural
verification only, tracked in §34 for a future round's regular backfill, continuing the
same discipline established in Rounds 6-11.

## 17j. Ninth Batch — `0061` (Round 12, 15 tables) + Round 11's Leftover 9 Tables

**Migration:** `backend/alembic/versions/0061_rls_tier9_real_enforcement.py`
(`down_revision = 0060`). All 15 tables newly enforced, spanning five adjacent
marketing/operations domains:
- Nurture (3): `nurture_sequences`, `nurture_enrollments`, `nurture_activities`.
- Reactivation (2): `reactivation_campaigns`, `reactivation_candidates`.
- SEO (3): `seo_pages`, `seo_keywords`, `seo_opportunities`.
- Local/Reputation (3): `local_listings`, `local_reviews`, `local_reputation_events`.
- Vendor/Purchasing (4): `vendors`, `vendor_bills`, `purchase_orders`,
  `purchase_order_items`.

All 15 confirmed via a live query (before writing the migration) to have `tenant_id`
indexed and `NOT NULL`, and to carry no pre-existing policy at all.

**Migration-cycle validation:**
- `alembic upgrade head` (0060 → 0061) on the long-lived migrated database: exit 0, no
  errors. `pg_policies`/`pg_class` confirmed all 15 tables have exactly the 4 expected
  real policies, `relrowsecurity = true`.
- `alembic downgrade 0060` (0061 → 0060): exit 0, no errors. Verified: all 15 tables have
  `relrowsecurity = false` again and zero policies — exact pre-migration state restored.
- `alembic upgrade head` again (0060 → 0061): exit 0, no errors, back to head.
- Separately, a clean `base → head` run (all 61 migrations) on a brand-new,
  never-before-touched database (`klaros_round12_fresh`): exit 0, no errors.

**Real-Postgres check suite — 52/52 passed**, combining this round's own spot-check with
a full backfill of Round 11's verification debt:
- **5-table spot-check sample from this round's own batch, one per domain**
  (`nurture_sequences`, `seo_pages`, `local_listings`, `vendors`, `purchase_orders`):
  SELECT isolation both directions, fail-closed NULL context, blocked cross-tenant UPDATE
  as a genuine 0-row no-op.
- **All 9 of Round 11's previously-only-structurally-verified tables, now individually
  isolation-proven** (`referral_programs`, `referral_rewards`, `referrals`,
  `retention_campaigns`, `retention_enrollments`, `retention_opportunities`,
  `outbound_sequences`, `outbound_steps`, `outbound_activities`) — same 4-check
  treatment each. The Outbound family required chaining through its full real FK
  dependency graph (`outbound_lists` → `outbound_contacts`, `outbound_sequences` →
  `outbound_steps`, then `outbound_enrollments` → `outbound_activities` referencing both
  a contact and a step) — the deepest FK chain proven so far, no new finding beyond
  continued diligence.

**Running total across all nine migrations (`0053`-`0061`, 106 tables):** all 106 have
structural verification; 96 of those now also have an individual real-Postgres isolation
proof (Round 11's running total of 82, plus all 9 of `0060`'s leftovers proven this
round, plus 5 from `0061`'s own spot-check this round). The remaining 10 — this round's
own non-spot-checked remainder of the batch (`nurture_enrollments`,
`nurture_activities`, `reactivation_campaigns`, `reactivation_candidates`,
`seo_keywords`, `seo_opportunities`, `local_reviews`, `local_reputation_events`,
`vendor_bills`, `purchase_order_items`) — have structural verification only, tracked in
§34 for a future round's regular backfill, continuing the same discipline established in
Rounds 6-12.

## 17k. Tenth Batch — `0062` (Round 13, 13 tables) — including `events`, with special discovery-interaction proof

**Migration:** `backend/alembic/versions/0062_rls_tier10_real_enforcement.py`
(`down_revision = 0061`). All 13 tables newly enforced:
- Retention/CRM (4): `advocate_candidates`, `customer_lifecycle_profiles`,
  `customer_risk_signals`, `collection_actions`.
- AI/Ops observability (3): `ai_invocation_logs`, `completion_packets`,
  `communication_logs`.
- Marketing (1): `campaign_conversions`.
- Event infrastructure (3): `events`, `event_processing_records`, `dead_letter_events`.
- Knowledge (2): `knowledge_files`, `knowledge_chunks`.

All 13 confirmed via a live query (before writing the migration) to have `tenant_id`
indexed and `NOT NULL`.

**The `events` special case.** `events` already carried a `discovery_select` policy
(`FOR SELECT TO klaros_discovery USING (true)`), created in Round 4 by
`provision_discovery_role.py` for the EventBus stuck-event discovery path (§11b) — at
that time `events` had no RLS enabled at all, so the policy existed in `pg_policies` but
was inert (a policy has zero effect until `ENABLE ROW LEVEL SECURITY` is set on its
table). This migration does not touch `discovery_select` in any way — it is not dropped,
not recreated, not referenced — the migration only adds the 4 new `tenant_*` policies.
Structural verification after `upgrade head` confirms `events` now carries exactly 5
policies: `discovery_select` (scoped `TO klaros_discovery` only, unchanged) plus the 4
new `tenant_select`/`tenant_insert`/`tenant_update`/`tenant_delete` (scoped to `public`,
i.e. every other role including `klaros_app`).

**Real-Postgres proof that both mechanisms coexist correctly** (part of this round's
148-check combined suite, see §17l for the full count):
- An ordinary tenant session (`klaros_app`) gets **normal tenant-scoped isolation**:
  tenant A sees only its own event, tenant B sees only its own, no-context session sees
  zero rows (fail-closed), a cross-tenant UPDATE attempt affects 0 rows.
- `klaros_discovery` **still gets its exact same narrow cross-tenant read**: fetching
  `id, tenant_id, event_type, status, created_at` returns rows from BOTH tenants (the
  entire point of the discovery mechanism), completely unaffected by the new tenant
  policies now being active.
- `klaros_discovery` **still cannot read `payload`** (column-level grant, unaffected by
  the new row-level policies) and **still cannot UPDATE/DELETE/INSERT** at all (RLS
  being newly enabled grants it no DML capability it didn't already lack).
- `klaros_app` (an ordinary tenant A session) does **NOT** see tenant B's row —
  confirming `discovery_select`'s permissiveness does not leak to any role it doesn't
  explicitly name (it names only `klaros_discovery`).

This is the first real-Postgres proof of the `discovery_select` mechanism interacting
with a table's real enforcing RLS being enabled in the SAME migration the table's policy
predates — every other discovery-grant table (`automations`, `agents`, `agent_versions`,
`agent_executions`, `automation_versions`) had its `discovery_select` policy added in the
*same* round its real RLS was added (Round 5), so this ordering (`discovery_select`
first in Round 4, real RLS four rounds later in Round 13) was untested until now. Result:
no conflict, no special-casing needed — the permissive-policy-OR design from §11c holds
exactly as designed regardless of which policy was added first.

**Migration-cycle validation:** `alembic upgrade head` (0061→0062): exit 0, all 13
tables confirmed with the 4 expected real policies (`events` with 5, as above).
`alembic downgrade 0061` (0062→0061): exit 0, exact pre-migration state restored —
`events` reverts to `relrowsecurity=false` with `discovery_select` still present but
inert again, matching its exact Round-4-through-Round-12 state. `alembic upgrade head`
again: exit 0, back to head. Separately, a clean `base → head` run (all 62 migrations)
on a brand-new database (`klaros_round13_fresh`): exit 0, no errors.

## 17l. Eleventh (FINAL) Batch — `0063` (Round 13, 13 tables) — TABLE COVERAGE COMPLETE

**Migration:** `backend/alembic/versions/0063_rls_tier11_final_batch.py`
(`down_revision = 0062`). The final 13 tables: `lead_attributions`, `licenses`,
`marketing_spend_allocations`, `operations_exceptions`, `outbound_enrollments`,
`review_requests`, `scope_changes`, `service_reminders`, `team_invites`,
`tenant_tool_policies`, `voice_receptionist_settings`, `warranties`,
`writeoff_requests`. All confirmed via a live query to have `tenant_id` indexed and
`NOT NULL`.

**Migration-cycle validation:** identical rigor to every prior migration —
`alembic upgrade head` (0062→0063): exit 0, all 13 tables confirmed with the 4 expected
real policies. `alembic downgrade 0062` (0063→0062): exit 0, exact pre-migration state
restored (all 13 back to `relrowsecurity=false`, zero policies). `alembic upgrade head`
again: exit 0, back to head. Separately, a clean `base → head` run of the **complete
63-migration chain** on a brand-new database (`klaros_final_fresh`): exit 0, no errors.

**Live confirmation of 132/132 coverage:** a direct query against `klaros_final_fresh`
(fresh, never touched by `conftest.py`, migrated purely via real `alembic upgrade head`)
counting every `public` table with a `tenant_id` column and a `tenant_select` policy
returns exactly **132 real-RLS tables, 0 with no RLS, 0 in audit-mode**. This is the
live, authoritative confirmation of full table coverage — not a running tally computed
from memory.

## 17m. Full Backfill Completed This Round — Every Table Now Individually Proven

Departing from the "spot-check the new batch, backfill the prior round's leftovers"
pattern used every round since 6 (which would normally leave `0062`/`0063`'s own
non-spot-checked tables and this round's leftover backfill debt for a future round),
**this round ran enough real-Postgres checks across three check-runs (64 + 52 + 32 = 148
checks) to give every one of the 26 newly-added tables (`0062` + `0063`) an individual
isolation proof, and also fully backfilled Round 12's 10 outstanding `0061` leftovers**
(`nurture_enrollments`, `nurture_activities`, `reactivation_campaigns`,
`reactivation_candidates`, `seo_keywords`, `seo_opportunities`, `local_reviews`,
`local_reputation_events`, `vendor_bills`, `purchase_order_items`) — all passed, no
failures, no new findings beyond the `events`/discovery interaction proof (§17k) and
minor test-construction details already seen in prior rounds (explicit JSON/NOT NULL
defaults raw SQL needs that the ORM normally supplies).

**Result: as of this round, all 132 of 132 TENANT_SCOPED tables have an individual
real-Postgres isolation proof (SELECT both directions, fail-closed NULL context, blocked
cross-tenant UPDATE as a genuine 0-row no-op) — not just structural `pg_policies`
verification.** This was a deliberate one-time departure from the incremental
per-round backfill pattern, done specifically to close out the table-coverage phase of
this work cleanly (no accumulated verification debt) before starting the substantially
different full-test-matrix phase the coordinator asked for next (§36).

## 18. CashForecast Fix — DONE this round, tested against real Postgres

**Bug (confirmed real, now fixed):** `CashForecastService.weekly_projection` in
`backend/app/services/cash_forecast_service.py` previously resolved the forecast via a
bare `session.get(CashForecast, forecast_id)` with no `tenant_id` check, so a caller who
knew (or guessed/enumerated) another tenant's `forecast_id` could read that tenant's
`starting_cash`, `starting_cash_source`, `generated_at`, and real `tenant_id` — while the
forecast's line ITEMS were already correctly tenant-filtered (so the leak was metadata,
not line-item detail, but still a real cross-tenant disclosure and the exact bug the
brief named).

**Fix applied:**
- `backend/app/services/cash_forecast_service.py`: added `CashForecastNotFoundError`
  and changed the lookup to
  `select(CashForecast).where(CashForecast.id == forecast_id, CashForecast.tenant_id == tenant_id)`,
  raising `CashForecastNotFoundError` when no row matches — cross-tenant and
  nonexistent IDs are now indistinguishable (fail closed, no existence disclosure).
- `backend/app/api/v1/cash.py`: imports the new exception and converts it to
  `HTTPException(404)` at the one existing caller (`POST /cash/forecast/generate`,
  which only ever passes its own freshly-created forecast's own id, so this path is
  unreachable in practice today but now fails closed rather than 500ing/leaking if it
  ever becomes reachable, e.g. once a `GET /cash/forecast/{id}` endpoint is added).
- `backend/tests/test_tenant_context_cash_forecast_service_phase17b2r.py`: replaced the
  old test that *documented* the leaky behavior with
  `test_tenant_a_forecast_isolated_from_tenant_b` (now asserts
  `CashForecastNotFoundError` is raised when tenant B requests tenant A's forecast id,
  and that tenant A can still read its own forecast fine) and added a new
  `test_weekly_projection_unknown_forecast_id_not_found` (nonexistent id also raises).

**Verification (real Postgres, not SQLite):** started a disposable `pgserver`-backed
PostgreSQL 16 instance, pointed `DATABASE_URL` at it, ran:
- `backend/tests/test_tenant_context_cash_forecast_service_phase17b2r.py` — **3 passed**
  (the tenant-context-stamping test, the new cross-tenant-isolation test, and the new
  unknown-id test).
- `backend/tests/test_finance_domain.py` (full file, includes the other CashForecast
  caller `test_cash_forecast_not_connected_without_manual_starting_cash`) — **7 passed,
  0 failed.**

No other test file references `CashForecastService`/`weekly_projection`
(`grep -rln "cash_forecast\|CashForecast" backend/tests/*.py` → only these two files).

## 19-29. RLS Tests (full matrix) / Restricted-Role / Pool-Reuse / Concurrency / Application E2E / Medical Tourism / Website / MCP / Webhook / Agent / Migration-Cycle

**Round 4 update:** the actual `0052`/`0053` migration upgrade→downgrade→upgrade cycle
is now proven clean (see §17b) — a real result, not the raw-SQL proxy Round 3 relied on.
Pool-reuse, concurrency, restricted-role, and NULL/malformed-context tests: **DONE
against the real migrated schema this round**, still only on the same 5-table subset
(§17b, 43/43 passed) plus the new `klaros_discovery` role checks (§11b, 15/15 passed).
The **full** 100+-table version of these tests, and all of Medical Tourism/Website/MCP/
Webhook/Agent E2E validation, are still NOT run — correctly out of scope for this round.

**Migration-cycle validation: a real correction to Round 2's report.** Round 2 reported
a "0048 downgrade bug" (`DROP INDEX ix_mcp_client_credentials_tenant_status` failing
with `UndefinedObjectError`) as a genuine, independent migration bug. Re-investigated
this round: **that was a false alarm**, caused by the identical root issue already
identified once in Round 2 (§1) — the database that failed downgrade had been built by
`conftest.py`'s `Base.metadata.create_all()` (SQLAlchemy's own declarative index-naming),
not by running the real Alembic migrations, so its index names never matched what
`0048`'s hand-written `downgrade()` expects to find under those exact literal names. On
a database built purely by running real `alembic upgrade head` from an empty database
(`klaros_rls_probe`), the full cycle was re-tested this round and is clean:
- `alembic upgrade head` (base → 0051): exit 0, no errors.
- `alembic downgrade 0039` (0051 → 0039, passing through 0048): **exit 0, no errors** —
  the exact `DROP INDEX ix_mcp_client_credentials_tenant_status` step that failed before
  now succeeds, because the index exists under that exact name (it was created by the
  real `0048` migration's own `op.create_index(...)` call, matching its own
  `op.drop_index(...)` call exactly — reading the migration file directly, both calls
  always used the identical literal name; there never was a real mismatch in the
  migration file itself).
- `alembic upgrade head` again (0039 → 0051): exit 0, no errors, back to head.

**Answering the coordinator's specific question — does 0048 block this phase's own
migration validation:** No. There is no real 0048 downgrade bug at all; it was a false
alarm from testing methodology, not a defect in the migration file. This phase's own
future RLS migrations (going forward from head, and their own downgrades) are not
blocked by this. The corrected, true state of the existing migration chain (0001-0051)
is that upgrade→downgrade→upgrade already works cleanly when tested correctly (via real
Alembic, not via `conftest.py`'s schema-builder fixture) — this is a second, independent
confirmation of the same lesson Round 2 drew from the RLS-audit-mode false alarm: **any
future validation in this phase must provision its test database via real
`alembic upgrade head`, never via `conftest.py`'s `_reset_database` fixture**, which is
correct and sufficient for ordinary application tests but silently invalid for anything
that depends on migration-authored DDL (RLS policies, index names, or downgrade
behavior).

## 30. Full Regression

Not run this round (full backend/frontend suite) — out of scope for this round's
4-item list; the CashForecast-relevant slice (`test_finance_domain.py`,
`test_tenant_context_cash_forecast_service_phase17b2r.py`) was run and is green (§18).

## 31. Pre-existing Failures

None found in anything actually tested this round. The Round 2 "0048 downgrade bug" is
retracted (Round 3, §19-29) — it was never a real pre-existing failure, only a
methodology artifact, now confirmed a third time via the identical conftest-vs-real-
migration pattern (§17b's Round 4 update).

## 32. Known Limitations of This Round (Round 13)

- Full per-table classification was captured as live query results in Round 2, with a
  total-count arithmetic correction in Round 6 (§4-9 addendum: true total is 132, not
  125) — the raw table-by-table list itself still has not been pasted verbatim into this
  document; regenerate via the `pg_class`/`information_schema.columns` query pattern
  against a freshly `alembic upgrade head`-ed instance if needed for the final
  deliverable.
- System-discovery architecture is **decided** (§11) and grants cover all 5 of 5 paths at
  the database level (§11b/§11c/§17k) — the live application code at all 5 call sites
  still was NOT rewired, per the coordinator's explicit instruction to keep this
  deferred.
- **Tenant-isolation RLS policy pattern is now real, tested migrations (`0052`-`0063`)
  for ALL 132 of 132 tenant-owned tables** (§17b-§17m) — table coverage is COMPLETE.
  Audit-mode remains fully retired (zero tables in that category, unchanged since Round
  9). Every table also now has an individual real-Postgres isolation proof, not just
  structural verification (§17m) — the incremental backfill debt pattern used since
  Round 6 is fully closed out.
- `FORCE ROW LEVEL SECURITY` remains untouched anywhere (correct — still out of scope).
- The two new migrations (`0062`, `0063`) are untracked working-tree files — not staged,
  not committed, per instruction.
- **The full test-matrix gate is NOT complete** (§36): a static bypass-mechanism search
  and a grep-based secret scan were done this round (both clean, no findings), but the
  full backend regression suite (338 test files, not run), a frontend confirmation pass
  (not run), and all 5 E2E validations (Medical Tourism/Website/MCP/Webhook/Agent, none
  run) remain substantial, genuinely separate work for a future round — correctly not
  compressed into this round per the coordinator's explicit instruction.
- This phase is explicitly, honestly **NOT COMPLETE** — table coverage finishing does
  not change that; the completion bar is the full gate in §36, which this round only
  started.

## 33. Security Risks Remaining

Substantially improved this round: real per-tenant RLS enforcement now exists, as
working-tree migrations not yet committed, on **132 of 132 (100%) tenant-owned tables**
(up from 106) — every tenant-owned table in the schema now has real, tested,
individually-proven row-level tenant isolation at the database layer, enforced under the
actual restricted `klaros_app` runtime role. The `klaros_discovery` mechanism continues
to provide real, tested narrowing for all 5 of 5 cross-tenant discovery paths at the
database level, now including `events`' real (not inert) interaction with its
pre-existing discovery policy (§17k). The live application code for all 5 discovery
paths still runs unchanged — no discovery path's actual runtime behavior has changed
yet, deliberately deferred. The CashForecast cross-tenant metadata leak remains fixed
and verified. A static search found no RLS-bypass mechanisms, no duplicate
tenant-context mechanism, and no superuser-at-runtime use in application code (§36); a
grep-based secret scan found no real secrets in anything this phase touched (§36). None
of this round's work has been committed, pushed, or wired into any running code path —
it exists as proven, working migrations in the working tree only. **The remaining risk
is entirely in the unvalidated gate, not in the RLS work itself**: without the full
backend regression suite, frontend confirmation, and E2E validation across all 5
systems, there is no proof yet that turning on RLS everywhere hasn't broken some
legitimate application code path (a query that unknowingly depended on seeing
cross-tenant data, a missing `set_tenant_context` call somewhere in the ~200+ existing
call sites, etc.) — that is exactly what §36's remaining items are for.

## 34. Exact Prerequisites for Next Round

**Table coverage work is DONE — no more RLS migrations are needed.** The next round(s)
should move entirely to §36's remaining gate items:

1. **Run the full backend regression suite** (`backend/tests/`, 338 test files) against
   a database provisioned the correct way (real `alembic upgrade head`, never
   `conftest.py`'s `_reset_database` fixture — confirmed multiple times across Rounds
   2-5 to silently diverge from real Alembic/RLS state). Compare failures against the
   Phase 17B-3 baseline the coordinator named, to distinguish "a pre-existing failure
   unrelated to this phase" from "a real regression this phase's RLS rollout caused."
   Given the conftest pitfall, expect some of the 338 files to need special handling (a
   dedicated Alembic-migrated database) the same way `test_restricted_app_role_cutover.py`
   already does — this is likely the single largest remaining risk area, since most of
   those 338 files were written against the old (no-RLS-enforcement) assumption.
2. **Run a frontend typecheck/tests/build confirmation** (`frontend/`) — not yet
   inspected in any round of this phase; establish what tooling exists
   (package.json scripts) before running.
3. **Run the 5 E2E validations**: Medical Tourism, Website, MCP, Webhook, and Agent —
   real end-to-end exercises of each system against the now-fully-RLS-enforced schema,
   through the real restricted `klaros_app` role. These are the parts of the brief this
   phase has not touched at all yet.
4. Rewire the 5 real call sites (`automation_service.py`, `agent_trigger_service.py`,
   `morning_brief_service.py`, `agent_recovery_service.py`, `bus.py`) to actually open a
   `klaros_discovery`-bound session — still deliberately deferred purely for its own
   higher risk profile, not blocked on anything else. Consider doing this only after
   items 1-3 above give confidence the rest of the system is stable under real RLS.
5. If a deeper static-analysis pass (AST-based, not grep-based) or a real
   secret-scanning tool becomes available in a future environment, re-run the bypass
   search and secret scan from §36 with that tooling for stronger confidence than this
   round's grep-based approximation provided.
6. **Only declare COMPLETE once every item above is genuinely, verifiably true** — table
   coverage alone (already done) is necessary but not sufficient, per the coordinator's
   repeated explicit instruction across Rounds 12 and 13.
7. When computing "how many tables total" in any future report, use **132** as the
   TENANT_SCOPED denominator (§4-9's Round 6 correction), not the earlier "125" figure
   that was carried in this log from Round 2 through Round 5.

## 35. Running Per-Table Tally (update each round — pattern matches 17B-2R's inventory doc)

**TABLE COVERAGE IS NOW COMPLETE: 132 of 132.** Of the **132** TENANT_SCOPED tables
(§4-9 addendum; corrected in Round 6 from a previously-miscounted 125, a pure
arithmetic error that did not affect any actual per-table classification):

| State | Count | Tables |
|---|---|---|
| **Real enforcing RLS (migration-backed)** | **132** | All 132. `0053`-`0061` (Rounds 4-12): 106 tables, listed in full in Round 12's version of this table (preserved in git history of this log's prior state; see §17b-§17j for the per-batch breakdown). `0062` (Round 13): `advocate_candidates`, `customer_lifecycle_profiles`, `customer_risk_signals`, `collection_actions`, `ai_invocation_logs`, `completion_packets`, `communication_logs`, `campaign_conversions`, `events`, `event_processing_records`, `dead_letter_events`, `knowledge_files`, `knowledge_chunks`. `0063` (Round 13, FINAL): `lead_attributions`, `licenses`, `marketing_spend_allocations`, `operations_exceptions`, `outbound_enrollments`, `review_requests`, `scope_changes`, `service_reminders`, `team_invites`, `tenant_tool_policies`, `voice_receptionist_settings`, `warranties`, `writeoff_requests`. |
| Still Phase-0 permissive audit-mode (`USING(true)`) — not yet converted | **0** | None remain (unchanged since Round 9). |
| No RLS at all yet | **0** | None remain — table coverage complete as of Round 13's `0063`. |

**Individual real-Postgres isolation-proof status: also 132/132, complete as of this
round** (§17m) — every table has been individually SELECT/fail-closed/blocked-UPDATE
proven against real Postgres, not merely structurally verified via `pg_policies`. This
departs from (and closes out) the incremental per-round backfill pattern used since
Round 6.

System-discovery role (`klaros_discovery`) grant coverage, of the 5 real discovery paths
(§10) — all 5 granted at the database level since Round 5, unchanged this round except
`events`' underlying RLS state:

| Path | Table(s) | Grant status | Underlying table RLS state |
|---|---|---|---|
| EventBus stuck-event discovery | `events` | Granted Round 4 (id, tenant_id, event_type, status, created_at — not `payload`) | **Real RLS since `0062` (Round 13)** — `discovery_select` policy now genuinely live for the first time (previously inert since Round 4, when `events` had no RLS at all); proven to coexist correctly with the new tenant policies (§17k). |
| Automation discovery | `automations`, `automation_versions` | Granted Round 5 (id/tenant_id/status/published_version_id; id/trigger_type/trigger_config/condition/steps) | Both real RLS since `0053`/`0054` — `discovery_select` policy live for both. |
| Agent discovery | `agents`, `agent_versions` | Granted Round 5 (id/tenant_id/status/current_version_id; id/triggers/instructions_snapshot) | Both real RLS since `0054` — `discovery_select` policy live for both. |
| Morning Brief discovery | `organizations` | Granted Round 5 (id/timezone/morning_brief_enabled/morning_brief_local_time/morning_brief_timezone) | No RLS (`organizations` is AUTH_BOUNDARY, no tenant_id — grant alone sufficient) |
| Agent Recovery discovery | `agent_executions` | Granted Round 5 (id only — already narrow in app code) | Real RLS since `0054` — `discovery_select` policy live. |

Live application code at all 5 call sites is still unrewired to use `klaros_discovery` —
unchanged from Round 4's decision, tracked as §34 item 2.

## 36. Full Test-Matrix Status (the gate before any COMPLETE claim)

Per the coordinator's explicit instruction, reaching 132/132 table coverage does not by
itself justify a COMPLETE claim. The full gate is: Medical Tourism/Website/MCP/Webhook/
Agent E2E validation, the full backend regression suite (compared against the Phase
17B-3 baseline), a frontend typecheck/tests/build confirmation, a secret scan, and a
static search for any RLS-bypass mechanism. Status as of Round 14 (this update):

- **Static search for bypass mechanisms — DONE (Round 13), clean.** Unchanged this
  round; see the Round 13 write-up below this line for the methodology.
- **Secret scan — DONE (Round 13), clean, grep-based caveat unchanged.**
- **Frontend typecheck/tests/build confirmation — DONE this round (Round 14), fully
  clean.** `npx tsc --noEmit`: exit 0, zero type errors. `npm test` (Vitest): **15/15
  test files, 89/89 tests passed**. `npm run build` (Next.js production build): exit 0,
  every route compiles and prerenders successfully. The coordinator predicted this would
  be "a clean no-op check since frontend wasn't touched" — confirmed exactly right; this
  phase's work is 100% backend (migrations + this round's 3 application-code fixes), so
  there was never a mechanism for it to affect the frontend.
- **Full backend regression suite — DONE this round (Round 14), against real Postgres
  with a genuinely `alembic upgrade head`-ed database, matching CI's own
  `DATABASE_URL`-is-real-Postgres setup.** See §37 for the full incident this run
  surfaced and how it was resolved. Final result after fixes: **2117 passed, 1 failed
  (the same pre-existing known voice flake named in the Phase 17B-3 baseline —
  `test_openai_realtime_voice_service.py::test_voice_stream_route_dispatches_to_realtime_engine_when_configured`),
  12 skipped** — matching the Phase 17B-3 baseline's shape exactly (2115/1/12; the +2
  passed vs. baseline is this phase's own earlier CashForecast test additions, not a
  discrepancy). **Zero new regressions from this phase's migration work.** Important
  caveat, stated plainly: this pytest run, like the CI job it mirrors, provisions each
  test's schema via `conftest.py`'s autouse `Base.metadata.create_all()`, which — as
  documented extensively since Round 2 — does not include RLS policy DDL. So this run
  proves "no application-code regression," not "every one of the 338 files' scenarios
  hold up under enforcing RLS." The real-RLS proof, and the one genuinely critical bug
  it found and this round fixed, came from a separate, purpose-built E2E pass (§37) —
  which is exactly why the coordinator asked for both a regression run AND E2E
  validation, not one or the other.
- **Medical Tourism/Website/MCP/Webhook/Agent E2E validation — DONE this round (Round
  14), against a genuinely `alembic upgrade head`-ed, fully-RLS-enforcing database,
  through the real restricted `klaros_app` role, calling the REAL application
  service-layer code (not hand-written raw SQL).** See §37 for the full write-up,
  including the critical finding (real RLS silently broke login/registration/every
  authenticated request — found, root-caused, and fixed as real application code, per
  the coordinator's explicit instruction never to weaken a policy to paper over this
  class of finding) and one still-open, deliberately-not-band-aided finding (MCP
  credential authentication's cross-tenant lookup). **20 checks, 19 passed, 1 still
  failing (MCP, tracked as real follow-up work, not fixed this round).**

**This phase remains explicitly NOT COMPLETE**, but is now much closer: the regression
suite is clean, frontend is clean, and E2E validation found (and mostly fixed) exactly
the class of real, RLS-enforcement-exposed application bug this whole gate exists to
catch — proof the gate is doing its job, not a formality. See §37 for the honest
remaining gap (the MCP finding) and §38 for next-round prerequisites.

## 37. Round 14 — The Real-RLS-Enforcement Incident: E2E Validation Finds a Critical Application Bug, Fixes It in Application Code

This section is the detailed write-up the coordinator asked for: root-cause every
regression-suite/E2E failure, classify it, never weaken a policy to make a test pass,
fix real application bugs in application code. Three distinct things happened this
round, in order.

### 37a. A process near-miss: reused a pytest-contaminated database for E2E, caught it, did not let it stand

The full backend regression suite (§36) was run first, against a database
(`klaros`, on a fresh `pgserver` instance) that had been correctly provisioned via real
`alembic upgrade head` beforehand. Immediately afterward, the first E2E test pass was
run against that SAME database — a mistake. `conftest.py`'s autouse `_reset_database`
fixture runs `Base.metadata.drop_all()` + `Base.metadata.create_all()` before every one
of the 2118 tests in that regression run, which (as documented since Round 2, and
independently reconfirmed at least three times since) wipes every RLS policy while
leaving `alembic_version` falsely stamped at head. A direct query confirmed it: **0 of
136 tables had RLS enabled, 0 policies existed anywhere**, despite `alembic_version`
correctly reading `0063`. This was caught immediately (the E2E suite's own Webhook
isolation check failed — tenant A could see tenant B's row — which is exactly the
symptom real contamination produces, not a subtle bug) rather than silently producing a
false "everything passed" result. **This is not a new finding — it is the same
documented pitfall (Rounds 2, 3, 4/13) — but it is the first time in this phase a round
almost fell into it in practice rather than just avoiding it by design.** Fixed by
switching the E2E run to `klaros_final_fresh`, a database that had only ever had real
`alembic upgrade head` run against it, never touched by any pytest run. This reinforces
§34/§38's standing instruction: a database that has had `pytest` run against it must
never be reused for anything that depends on real RLS state again — a plain regression
run and a real-RLS E2E run must always use physically separate databases, not just
separate test files.

### 37b. Real finding #1 (FIXED): `session.commit()` silently drops tenant context for any later read on the same session — a systemic, previously-latent bug real enforcement newly exposes

**Symptom:** `MedicalTourismService.create_provider()` — the very first real E2E
check — failed with `InvalidRequestError: Could not refresh instance`, thrown by
`await session.refresh(provider)` immediately after `await session.commit()`, in
otherwise completely ordinary, unremarkable service code.

**Root cause, confirmed with a minimal, isolated repro:** `set_tenant_context` uses
`SET LOCAL` (`set_config(..., true)`) — transaction-scoped by design, and correctly so
(this is what makes it safe against leaking across a pooled connection's reuse by an
unrelated later tenant — see the function's own long-standing docstring). But
`session.commit()` ends that transaction. This app's `async_session_maker` is
constructed with `expire_on_commit=False`, so a great many service methods across the
codebase call `session.commit()` and then immediately do one more read on the SAME,
still-open session — most commonly `session.refresh(obj)` to pick up server-generated
column defaults, but also occasionally a follow-up `session.execute(select(...))`.
SQLAlchemy transparently opens a NEW transaction for that next statement. That new
transaction never received the `SET LOCAL` the original transaction did — `current_setting('app.tenant_id', true)` genuinely returns empty at that point, confirmed with a two-line repro
script run directly against a real, RLS-enforcing database through `klaros_app`. Under
real enforcing RLS, the row the caller's own session just committed is fail-closed
invisible to the very next read in the same session.

**Why this was invisible until now:** every RLS policy in the schema was audit-mode
(`USING(true)`) or simply didn't exist until this phase's own migrations. Under either
condition, a query's visibility never depended on `app.tenant_id` at all, so this
transaction-boundary detail was a real, generic SQLAlchemy fact about the codebase that
had zero observable effect until the exact commit this phase's own `0053`-`0063`
migrations completed — table coverage reaching 100% is what finally gave this bug
something to bite on, in exactly the shape the coordinator predicted.

**Scope, confirmed by a precise script (not a rough guess):** parsing every
`app/services/*.py`, `app/api/v1/*.py`, and `app/events/*.py` file for `await
session.commit()` immediately followed (within a few lines, no intervening
`set_tenant_context` call or new `async with` block) by another read
(`session.refresh(`/`session.execute(`/`session.get(`) found **203 call sites across 61
files** — this is not a one-off, it is close to the majority of the service layer.

**The fix — in application code, not a policy weakening, and NOT 203 individual
edits.** Rather than touch every one of the 61 files (far higher regression risk than a
single, well-reasoned root fix), the fix lives entirely in
`backend/app/db/session.py`:
- `set_tenant_context` now also stores the `tenant_id` it stamps on
  `session.info["_klaros_tenant_context_tenant_id"]`.
- A new SQLAlchemy `after_begin` event listener (registered once, module-level, on
  `sqlalchemy.orm.Session` — this transparently covers `AsyncSession` too, since
  `AsyncSession` delegates to a wrapped sync `Session` internally) fires every time a
  NEW top-level transaction begins on any session — including the implicit one a
  post-commit `session.refresh()`/`session.execute()` opens — and, if that session has a
  stored tenant_id, transparently reissues the identical `SET LOCAL app.tenant_id`
  before the caller's own statement runs. Nested transactions (`SAVEPOINT`s within an
  already-active transaction) are explicitly skipped (`transaction.nested`), since the
  outer transaction's context is already correctly in effect for those.
- This fixes all 203 call sites at once, with zero changes to any of the 61 service/API
  files, and is a true no-op for every session that never calls `set_tenant_context` at
  all (the listener returns immediately when `session.info` has no stored tenant_id) —
  so it cannot affect any code path (cross-tenant discovery sessions, unauthenticated
  routes, etc.) that was never using tenant-scoped context to begin with.
- **Verified with the same isolated repro**: `current_setting('app.tenant_id', true)`
  now correctly still reads the right tenant_id immediately after `session.commit()`.
  Then re-verified by rerunning the full E2E suite (§37d) — every previously-failing
  case now passes.
- **Verified not to regress anything**: `tests/test_auth_and_tenant_isolation.py` +
  `tests/test_auth_refresh_and_logout.py` (10 tests) still pass unchanged against the
  default SQLite test engine (the listener is a real no-op there — dialect check).
  The full 338-file regression suite (§36/§37c) also still passes at the exact Phase
  17B-3 baseline shape after this change.

### 37c. Real finding #2 (FIXED): login, registration, and literally every authenticated request were silently broken by real RLS on `users` — the single most severe finding of this entire phase

**Symptom, found immediately after fixing 37b:** a direct call to
`app.services.auth_service.authenticate()` (the real login function) against a real
user seeded moments earlier, with the exactly correct password, returned `AuthError("Invalid organization, email, or password")` — the generic "wrong credentials" failure, indistinguishable from an actual wrong password.

**Root cause:** `users` has had real, enforcing RLS since Round 4's `0053` migration.
Three separate functions read or wrote `users` without ever calling
`set_tenant_context` first:
- `app/api/deps.py::get_current_user` — **the single dependency almost every
  authenticated endpoint in the entire API depends on** — decoded the JWT, then
  immediately ran `select(User).where(User.id == user_id)` **before** the line, later
  in the same function, that finally called `set_tenant_context(db, tenant_id)`. Under
  real RLS with no context yet set, that lookup finds nothing, every request looking
  like an invalid/expired token.
- `app/services/auth_service.py::authenticate` (login) — resolved the tenant's
  `Organization` by slug (fine — `organizations` has no RLS, it IS the tenant root),
  then queried `users` **without ever calling `set_tenant_context`** — same fail-closed
  outcome, reported to the caller as a wrong password.
- `app/services/auth_service.py::refresh_access_token` — same shape, using the refresh
  JWT's own embedded `tenant_id` claim.
- `app/services/auth_service.py::register_organization` — creates the tenant's very
  first `User` row via `INSERT`, again with no tenant context ever set. Real RLS's
  `WITH CHECK (tenant_id = current_tenant_id())` on `INSERT` rejects this outright — a
  BRAND NEW TENANT COULD NOT EVEN FINISH SIGNING UP.

**Why this was invisible until now:** identical reasoning to 37b — with `users` in
audit-mode or with no RLS at all (true for every round of this phase before `0053`
existed, and in every environment/test run that never provisions the restricted
`klaros_app` role with genuine RLS in force), none of these four functions' queries
were ever filtered by `app.tenant_id` at all, so omitting `set_tenant_context` was
silently harmless. The instant `users` got real enforcing RLS (Round 4's `0053`), this
became a live, universal outage waiting to be observed by anything that actually
exercised real RLS + `klaros_app` together — which nothing before this round's E2E pass
ever did.

**The fix — in application code, exactly per the coordinator's instruction, no policy
touched:**
- `app/api/deps.py::get_current_user`: moved `tenant_id = uuid.UUID(payload["tenant_id"])`
  and the `set_tenant_context(db, tenant_id)` call to BEFORE the `select(User)` lookup,
  not after. Safe to trust `payload["tenant_id"]` at that point because `decode_token`
  already verified the JWT's signature earlier in the same function — the identical
  "trust it only after verification" reasoning `marketplace_lead_webhook` already uses
  for its own path `tenant_id` (after HMAC verification), not a new kind of trust
  boundary.
- `app/services/auth_service.py::authenticate`: added `await set_tenant_context(db,
  org.id)` immediately after resolving `org` by slug, before the `users` query. Safe
  because `org.id` is this exact query's own real result from the (RLS-free, root)
  `organizations` table, not client-supplied.
- `app/services/auth_service.py::refresh_access_token`: added the same call, using
  `refresh_payload["tenant_id"]` (from an already-signature-verified refresh JWT).
- `app/services/auth_service.py::register_organization`: added the same call
  immediately after `await db.flush()` gives the brand-new `org.id` its real value,
  before constructing/adding the `User` row.
- (`logout()` needed no fix: it runs via the same cached `db` session `get_current_user`
  already stamped, by FastAPI's own per-request dependency caching — confirmed, not
  assumed, by reading the route's dependency wiring in `app/api/v1/auth.py`.)

**Verified end-to-end, all three flows, via the real service functions, against real
RLS + `klaros_app`:** register → login → `get_current_user` (the actual FastAPI
dependency function, called directly, not simulated) — **all three now succeed**,
confirmed with a dedicated script before folding the checks into the main E2E suite
(§37d/§17n). Re-ran `tests/test_auth_and_tenant_isolation.py` +
`tests/test_auth_refresh_and_logout.py` (10 tests) against the default SQLite engine
afterward — still 10/10 passing, confirming the reordering didn't change behavior for
any already-covered case, only fixed the real-RLS one nothing had covered before.

### 37d. Real finding #3 (FOUND, NOT FIXED — deliberately, flagged as real follow-up work): MCP credential authentication's cross-tenant lookup is now broken by real RLS on `mcp_client_credentials`, and needs a real design decision, not a quick patch

**Symptom:** `McpCredentialService.authenticate(raw_token)` — which looks up a
credential purely by the SHA-256 hash of the presented token, deliberately never by any
client-supplied tenant claim (see its own docstring: this IS the tenant-resolution step
itself) — returns `None` (indistinguishable from "no such credential") even for a
freshly-issued, genuinely valid token, once `mcp_client_credentials` has real RLS
(since Round 8's `0057`).

**Root cause:** structurally identical to 37c's shape (a query that must run before any
tenant is known), but WITHOUT `authenticate`'s login analog's escape hatch: login could
bounce off `organizations` (a real, RLS-free root table keyed by a caller-supplied
slug) to resolve the tenant first, then set context, then query `users`. MCP credential
authentication has no equivalent non-tenant-scoped table to bounce off — the token hash
IS the only lookup key, and `mcp_client_credentials` itself is (correctly) real,
enforcing, tenant-scoped RLS, so there is no safe way to query it for "which tenant, if
any, owns this hash" without already being inside some tenant's context, which is
exactly the fact this call is trying to establish.

**Why this is NOT fixed this round, on purpose:** the correct fix is a real design
decision, not a one-line reorder like 37c's three fixes — most likely a 6th narrow,
read-only cross-tenant grant analogous to the `klaros_discovery` mechanism §11/§11c/§17k
already built and proved for the 5 named discovery paths (a role or grant scoped
specifically to `mcp_client_credentials(token_hash, tenant_id, id, status, ...)` for
authentication-resolution purposes only, never DML, matching that mechanism's own
"narrow, auditable, no blanket bypass" design constraints from the original brief). Per
the coordinator's own explicit instruction never to weaken a policy to make something
pass, and given this needs a genuine architecture decision (not a mechanical fix), it is
left open, honestly reported, and tracked in §38 as real follow-up work — the MCP
authentication path is currently broken for any tenant relying on it in an environment
where `klaros_app` + real enforcing RLS are actually in effect (i.e., not yet in
production today, since the restricted role cutover itself hasn't happened yet — see
§34 item 2 — but it would break the moment it did, without this fix).

### 37e. Files changed this round (application code, not migrations)

- `backend/app/db/session.py` — the `after_begin` event listener fix (37b).
- `backend/app/api/deps.py` — `get_current_user` reordering fix (37c).
- `backend/app/services/auth_service.py` — three `set_tenant_context` call additions
  (37c: `authenticate`, `refresh_access_token`, `register_organization`).

All three are working-tree, uncommitted changes, per instruction (no commits, no
pushes). None of the three touches any RLS policy, migration, or grant — every fix is
purely in how/when the application asserts its own already-known, already-trusted
tenant identity to the database, never a change to what the database enforces.

## 39. Round 15 — The MCP Credential Authentication Fix (§37d Closed Out)

Per the coordinator's explicit direction, following the `klaros_discovery` precedent
(§11/§11c) exactly: a 6th narrow, read-only, auditable grant, plus — for the first time
in this phase — real live-code wiring of a `klaros_discovery`-bound session (§34 item 2
has, since Round 5, deliberately deferred this for the other 5 discovery paths; this is
the first one actually done, for the reason §37d gave: unlike the other 5, this path
was found broken, not merely unproven).

**Grant (`backend/scripts/db/provision_discovery_role.py`):** added
`"mcp_client_credentials": ("id", "tenant_id", "token_hash", "status")` to
`_TABLE_COLUMN_GRANTS` — `token_hash` is the lookup key, `status` lets the resolution
step early-reject a REVOKED credential, `id`/`tenant_id` are the two columns the caller
actually needs. Every other column (`name`, `role`, `created_by`, `revoked_at`,
`last_used_at`, ...) deliberately NOT granted. The script's existing per-table
`discovery_select` policy loop (§11c) covers this table automatically — no new code
needed there.

**Live-code wiring (new, not just a database-level grant):**
- `backend/app/core/config.py`: new `DISCOVERY_DATABASE_URL: str | None = None` setting,
  documented as intended for `klaros_discovery`, optional/unset-safe.
- `backend/app/db/session.py`: new `discovery_engine`/`discovery_session_maker` — a
  SEPARATE engine/pool from the ordinary `engine`/`async_session_maker`, `None` when
  `DISCOVERY_DATABASE_URL` isn't configured.
- `backend/app/services/mcp_service.py::McpCredentialService.authenticate`: rewritten as
  two real steps when discovery is configured — (1) `_resolve_tenant_id_via_discovery`
  resolves the tenant via the narrow `klaros_discovery` session (read-only, no write);
  (2) a genuine per-tenant `klaros_app` session, `set_tenant_context`-stamped with the
  resolved tenant_id, does the real work (full-row fetch + the `last_used_at` write)
  under real RLS. When `DISCOVERY_DATABASE_URL` is unset (every environment that hasn't
  provisioned `klaros_discovery` yet, including this codebase's own default SQLite test
  suite), falls back to the exact pre-Round-15 single-query behavior, unchanged — this
  is what makes the change additive rather than a new prerequisite every environment
  must configure before MCP keeps working.

**A real regression found and fixed during this round's own testing, before landing:**
the first version of this fix made `authenticate()` unconditionally require
`klaros_discovery` (fail closed with no fallback), which broke 14 of the 19 real tests in
`tests/test_mcp_server.py` — every one of them runs against the default SQLite engine,
which never configures `DISCOVERY_DATABASE_URL`. Caught by re-running the existing test
suite before considering the fix done (not by assuming a real-Postgres E2E pass was
sufficient), root-caused correctly, and fixed with the graceful fallback described above
— restoring `tests/test_mcp_server.py` to 19 passed / 5 skipped (the 5 are the file's own
real-Postgres-only tests, correctly skipped outside that environment) and confirming the
fallback path itself is not a weaker security posture (§39's code comments spell out
why: it's the exact behavior every environment already had, and an environment that HAS
cut over to real RLS but hasn't yet provisioned `klaros_discovery` still fails closed
under the fallback, for the same reason every other un-provisioned discovery table did
before this round).

**Real-Postgres validation — two layers, same rigor as every other `klaros_discovery`
grant:**
- **Database-level (12/12 checks passed):** `klaros_discovery` resolves an ACTIVE
  credential's `tenant_id`/`id` via `token_hash`; can see (but the application layer, not
  the grant, is what filters on) a REVOKED credential's status; an unknown token hash
  resolves to nothing; cannot read `name`/`role`/any other ungranted column; cannot
  `SELECT *`; cannot UPDATE/DELETE/INSERT; still cannot read `users`/`customers`/
  `invoices` (no grant at all on those, confirming the role's reach is exactly this one
  table's 4 columns, nothing implicit).
- **Real service-level, end-to-end (via the actual `McpCredentialService`, not raw
  SQL):** issued a real credential for tenant A through `klaros_app`; called the real
  `authenticate()` method with the raw token; confirmed it resolves the correct
  `tenant_id`/`id` and genuinely updates `last_used_at` (proving the write half, under
  real per-tenant RLS, actually happens); confirmed an unknown/malformed token still
  returns `None` (fail-closed, no oracle); confirmed tenant B's `list_credentials` still
  cannot see tenant A's credential (isolation unaffected by the fix, since step 2 still
  runs under ordinary real RLS).

**E2E suite re-run: 20/20 passed** (up from 19/20) — the MCP `authenticate()` check that
was the phase's one remaining open finding now passes for real, not band-aided.

**Files changed this round (all application code, zero policy/migration changes):**
`backend/app/core/config.py`, `backend/app/db/session.py`,
`backend/app/services/mcp_service.py`, `backend/scripts/db/provision_discovery_role.py`.

## 40. Final Completion-Gate Checklist

Every item from the original brief's completion gate, checked honestly, one at a time:

| Gate item | Status |
|---|---|
| All TENANT_SCOPED tables have real, enforcing RLS | **132/132 — DONE** (§17l/§35) |
| Every table individually isolation-proven (not just structurally verified) | **132/132 — DONE** (§17m) |
| Full backend regression suite run, understood, compared to Phase 17B-3 baseline | **DONE — 2117 passed, 1 failed (same pre-existing known voice flake), 12 skipped, run three times across this round (before fixes, after the auth/session fixes, after the MCP fix) with an identical, baseline-matching result every time; zero regressions** (§36/§37) |
| Frontend typecheck/tests/build confirmation | **DONE — 0 type errors, 89/89 tests, clean production build** (§36) |
| Medical Tourism E2E validation (real service code, real RLS, real `klaros_app`) | **DONE — clean** (§37/§17n) |
| Website E2E validation | **DONE — clean** |
| MCP E2E validation | **DONE — clean, as of this round's fix (§39)** |
| Webhook E2E validation | **DONE — clean** |
| Agent E2E validation | **DONE — clean** |
| Secret scan | **DONE — clean (grep-based; no dedicated tool available in this sandbox — see §36's caveat)** |
| Static search for RLS-bypass mechanisms | **DONE — clean (grep-based; no AST/linter-based tool used — see §36's caveat)** |
| CashForecast cross-tenant metadata leak | **Fixed and tested (Round 2)** |
| Migration upgrade→downgrade→upgrade cycle clean, every batch | **DONE — every one of `0052`-`0063`, individually and as the full chain** |
| `FORCE ROW LEVEL SECURITY` | **NOT enabled anywhere — correct, explicitly out of scope for this phase** |
| No commits, no pushes | **True for the entire phase, all 15 rounds** |

**One item from the ORIGINAL brief's scope is deliberately still open, and the
coordinator has explicitly confirmed this is a known limitation, not a blocker to
completion:** live application code at 4 of the 5 original `klaros_discovery` call
sites (`automation_service.py`, `agent_trigger_service.py`,
`morning_brief_service.py`, `agent_recovery_service.py` — everything except the MCP
path this round finally wired for real, and excepting `bus.py`/EventBus which was
proven at the database level in Round 4 but also never live-wired) still runs its
original, unscoped queries rather than opening a `klaros_discovery`-bound session. This
has been consistently flagged as deliberately deferred since Round 5 (§34 item 2,
repeated in every subsequent round) — those 4 paths' database-level grants are real,
tested, and correct (§11b/§11c); only the application-code rewiring to actually use
them is undone. This does not weaken any RLS guarantee this phase makes (those 4 paths'
own tables already have real, enforcing RLS regardless of whether the discovery role is
wired into their read path) — it is purely about whether those specific cross-tenant
discovery sweeps get the narrower column-level view `klaros_discovery` would give them,
versus their current full-row reads through the unrestricted path they've always used.

## PHASE 17B-4: COMPLETE WITH LIMITATIONS

Every item in the original brief's completion gate that this phase's own scope commits
to is now genuinely, verifiably true: 132/132 tables with real, individually-proven RLS
enforcement; a clean full backend regression suite matching the Phase 17B-3 baseline; a
clean frontend; all 5 named E2E systems (Medical Tourism, Website, MCP, Webhook, Agent)
validated clean through real service code under real RLS via the real restricted
`klaros_app` role; a clean static bypass-mechanism search; a clean secret scan; the
CashForecast fix; a clean migration cycle for every one of the 12 migrations this phase
added (`0052`-`0063`); and `FORCE ROW LEVEL SECURITY` correctly never enabled. Along the
way, this phase's own validation work found and fixed the single most severe bug
possible for a project at this stage — real RLS enforcement silently breaking login,
registration, and nearly every authenticated API request — before it ever reached a
real deployment, which is exactly what this entire validation gate existed to catch.

**The one honest limitation:** 4 of the 5 original `klaros_discovery` cross-tenant
discovery paths (Automation, Agent, Morning Brief, Agent Recovery) have real, tested
database-level grants but have NOT been rewired into live application code to actually
use them — deliberately deferred since Round 5 as a distinct, higher-risk, separate
change, and explicitly confirmed by the coordinator as a known limitation rather than a
blocker to this phase's own completion, since the original brief's completion gate does
not require it.

No commits, no pushes, no `FORCE ROW LEVEL SECURITY`, no Phase 17B-5 work — across all
15 rounds of this phase, exactly as instructed throughout. HEAD remains
`af4937e403e47cdc141f2db349dfcc46a3df6c4b`.
