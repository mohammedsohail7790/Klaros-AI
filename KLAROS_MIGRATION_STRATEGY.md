# Klaros AI — Migration Strategy

Covers item EE in full, aggregating the per-change strategies scattered across the Final architecture documents into one place, plus the general principle applied throughout.

## General principle

No migration in this entire roadmap is designed as a big-bang cutover. Every major architectural change follows: **Current → Compatibility layer → Migration → Dual read/write if required → Validation → Cutover → Deprecation.** This was checked against every major change proposed across the 20 target documents and this review's own corrections; the table below is the complete list.

## Per-change migration safety plan

### 1. `Organization.autonomy_level` deprecation
- **Current:** inert field, defaulted, never read.
- **Compatibility layer:** none needed — nothing reads it today, so nothing needs to keep working during a transition.
- **Migration:** none required now.
- **Dual read/write:** not applicable.
- **Validation:** CI grep-check (Phase 0.4) preventing new reads from being introduced.
- **Cutover:** the Agent autonomy tier model becomes the real enforcement point from Phase 7 onward — not a cutover *from* working behavior, since none existed.
- **Deprecation:** docstring-marked deprecated immediately; physical column drop deferred to at least one release cycle after Phase 7 ships, as its own standalone reversible migration.

### 2. Postgres RLS rollout (existing ~190 tenant-scoped tables)
- **Current:** app-layer-only manual `.where(tenant_id==...)` filtering.
- **Compatibility layer:** RLS in permissive/audit mode (Phase 0.2) — behaviorally a no-op, logs discrepancies only.
- **Migration:** per-table policy-adding migrations, smallest-blast-radius-first (5 tables in Phase 0, remainder ongoing through Phase 11).
- **Dual read/write:** the "dual read" here is running the existing app-layer filter and the new RLS policy simultaneously during audit mode — any row RLS would have blocked but the app-layer query returned anyway is a bug fixed *before* the bypass is removed for that table.
- **Validation:** mandatory tenant-isolation cross-tenant test suite, non-skippable in CI once a table is RLS-enabled.
- **Cutover:** bypass removed per-table, by sensitivity tier, only after a full audit-mode observation window (minimum one week in staging, per Phase 0.2's done-when criterion).
- **Deprecation:** the manual `.where()` convention is **never deprecated/removed** — it remains permanently as defense-in-depth alongside RLS. This is a deliberate departure from "eventually deprecate the old mechanism" because the old mechanism is not being replaced, only supplemented.

### 3. Integration marketplace taxonomy (new `IntegrationProviderCatalog` alongside existing `IntegrationConnection`)
- **Current:** `frontend/app/settings/integrations/page.tsx` hardcodes provider groupings client-side; no catalog table.
- **Compatibility layer:** the new catalog is purely additive read-side data; the existing `IntegrationConnection`-based connect/disconnect flow (Stripe key-based, QuickBooks/Google Calendar OAuth2) is completely untouched.
- **Migration:** new table + seed data (Phase 1.2), no change to `IntegrationConnection`'s schema.
- **Dual read/write:** not applicable — the frontend can be updated to read from the new catalog endpoint at its own pace (Phase 5) without the backend needing to support two response shapes simultaneously, since the catalog endpoint is genuinely new, not a replacement of an existing one.
- **Validation:** seed-data-matches-verified-reality regression test.
- **Cutover:** frontend switches from hardcoded groupings to catalog-driven rendering in Phase 5, in one deploy (safe because the catalog endpoint will have already been live and tested since Phase 1).
- **Deprecation:** the hardcoded client-side grouping constants are removed from the frontend once Phase 5 ships — the only genuine "old code removed" step in this entire strategy document, and it's low-risk because it's a pure UI-data-source swap with no schema or API-contract implication for anything else.

### 4. Agent Runtime insertion into `ToolRegistry.execute()`
- **Current:** 9-step pipeline, actor-agnostic but with no `AGENT` actor type or agent-specific checks.
- **Compatibility layer:** the 2 new checks (agent permission, agent autonomy tier) are inserted such that they are **no-ops for any non-agent actor** — a `USER` or existing `AI` actor's execution path is provably unaffected (KLAROS_FINAL_TESTING_ARCHITECTURE.md's "existing 9-step behavior unaltered for non-agent actors" test is the concrete validation of this).
- **Migration:** new tables only (`Agent`/`AgentVersion`/`AgentExecution`/`AgentToolPermission`), no change to any existing table's schema except the additive, nullable `AIInvocationLog.agent_execution_id`.
- **Dual read/write:** not applicable — no existing data is being moved or reinterpreted.
- **Validation:** the full agent-runtime test layer, plus the mandatory pre-autonomy security gate before any agent is allowed above Recommend tier.
- **Cutover:** not applicable in the traditional sense — this is new capability, not a replacement of old capability; "cutover" is simply the act of an agent's tier being raised past Observe/Recommend once its gate passes, which is a per-agent, reversible administrative action.
- **Deprecation:** not applicable.

### 5. Website Builder (new capability, no prior website-generation feature exists)
- **Current:** no website-builder feature exists at all.
- **Compatibility/Migration/Dual-read/Validation/Cutover/Deprecation:** all not applicable in the "replacing something" sense — this is purely additive new capability. The only migration-safety-relevant design choice is that publishing runs through the existing `ApprovalRequest` mechanism rather than a new one, so no new approval-system migration risk is introduced.

### 6. Dropshipping domain (genuinely new relational domain, explicitly not built on `Vendor`/`VendorBill`)
- **Current:** no product/order/supplier domain exists; `Vendor`/`VendorBill` serve an unrelated purpose (paying for services) and are confirmed insufficient (4/5 business columns, no catalog concept).
- **Compatibility/Migration:** entirely new tables (Phase 10), zero changes to `Vendor`/`VendorBill`.
- **Validation:** Dropshipping validation scenario (KLAROS_VALIDATION_SCENARIOS.md).
- **Cutover/Deprecation:** not applicable — nothing is being replaced.

### 7. `ReferralReward` additive columns (`currency`, `commission_basis`) for Medical Tourism
- **Current:** `ReferralReward` has no currency concept.
- **Compatibility layer:** both new columns nullable; existing referral rows remain valid with null values, interpreted at read time as the organization's base currency (a service-layer default, not a database default, so it can be changed later without a further migration).
- **Migration:** single additive-column migration, no backfill required (nullable).
- **Dual read/write:** not applicable.
- **Validation:** existing referral-reward tests continue passing unmodified; new tests cover the currency-aware path only for Medical Tourism's `ReferralCommission`.
- **Cutover:** Medical Tourism referrals populate the new columns from Phase 9 onward; existing non-Medical-Tourism referrals are never required to populate them.
- **Deprecation:** not applicable.

## Rollback posture, summarized

Every item above is individually, independently rollback-able (detail per-item in KLAROS_FINAL_DATABASE_ARCHITECTURE.md's rollback section and each phase's own rollback line in KLAROS_MASTER_IMPLEMENTATION_ROADMAP.md). No item in this roadmap requires rolling back multiple phases together — this is the direct benefit of the additive-only, registry-driven, FK-referencing design enforced throughout: because nothing forks or rewrites existing tables (with the two narrow, nullable exceptions above), rollback of any new feature is always "stop reading/writing the new tables," never "restore the old tables to a prior state."
