# Klaros AI — Final Database Evolution and Migration Architecture

Covers item P (aggregate view — per-entity detail lives in KLAROS_FINAL_DOMAIN_MODEL.md) plus migration safety (item EE).

## Current state, verified

SQLAlchemy 2.0 async (`asyncpg` prod, `aiosqlite` dev/test), Alembic, **39 migrations** (`0001`–`0039`, most recent: `0039_warranties.py`, `0038_licenses.py`, `0037_ai_kill_switch.py`, `0036_team_invites.py`, `0035_organization_billing.py`). ~208 models across 23 files. `pgvector` real (migration `0032_knowledge_pgvector.py`, HNSW index). Connection pool: `pool_size=10, max_overflow=20` (prod), a value increased in response to a real documented incident (matches git log "Fix: raise DB connection pool size to stop dashboard-load timeouts"). No RLS anywhere across all 39 migrations (confirmed by grep).

## New table families (aggregate list)

Blueprint domain: `BusinessBlueprint`, `BlueprintSection`, `BlueprintClaim`, `DiscoverySession`, `DiscoveryTurn`.
Recommendation domain: `Recommendation`.
Integration marketplace: `IntegrationProviderCatalog` (tenant-independent).
Agent runtime: `Agent`, `AgentVersion`, `AgentExecution`, `AgentToolPermission`.
Workflow generation: `Workflow`, `WorkflowVersion`.
Website builder: `Website`, `WebsiteVersion`, `WebsitePage`, `WebsiteSection`, `WebsiteFormBinding`.
Domain extensibility registry: `DomainDefinition`/`VerticalExtension`, `OrganizationVerticalExtension`.
Medical Tourism: `Provider`, `ProviderCredential`, `Procedure`, `ProviderProcedure`, `PatientLead`, `Consultation`, `ReferralCommission`.
Dropshipping: `Supplier`, `Product`, `SKU`, `InventorySnapshot`, `Order`, `OrderLine`, `SupplierOrder`, `Shipment`, `OrderReturn`.

Full purpose/relationships/indexes/constraints/versioning/audit spec for each is in KLAROS_FINAL_DOMAIN_MODEL.md — not repeated here.

## Additive-only changes to existing tables (exhaustively enumerated, confirmed minimal)

1. `AIInvocationLog.agent_execution_id` — nullable FK, links existing LLM-call trail to new `AgentExecution`.
2. `ReferralReward.currency`, `ReferralReward.commission_basis` — nullable, required for Medical Tourism's `ReferralCommission`, backward-compatible.

No other existing table is altered. This was checked against the explicit "must NOT be duplicated" list (Customer/Lead, Vendor/VendorBill, Appointment, ApprovalRequest, Invoice/Payment, IntegrationConnection, CompanyMemory) and confirmed clean.

## Migration sequencing (dependency-ordered)

1. `DomainDefinition`/`VerticalExtension` + `OrganizationVerticalExtension` (reference data other new tables' FKs depend on) — Phase 1.
2. `IntegrationProviderCatalog` (reference data, seeded from verified real/stub/normalizer status) — Phase 1.
3. `DiscoverySession`/`DiscoveryTurn` — Phase 2.
4. `BusinessBlueprint`/`BlueprintSection`/`BlueprintClaim` — Phase 3.
5. `Recommendation` — Phase 4 (depends on Blueprint claims and catalog existing as recommendation targets/sources).
6. `Agent`/`AgentVersion`/`AgentToolPermission`/`AgentExecution` + `AIInvocationLog.agent_execution_id` — Phase 7 (hard-gated behind the Phase 0 security gate, independent of its position in the table-dependency order — a table can be *creatable* in an earlier phase without being *usable for autonomous execution* until governance lands; this migration ships the schema only, with autonomy enforcement code landing in the same phase, never earlier).
7. `Workflow`/`WorkflowVersion` — Phase 8.
8. `Website`/`WebsiteVersion`/`WebsitePage`/`WebsiteSection`/`WebsiteFormBinding` — Phase 6.
9. Medical Tourism tables (depends on `VerticalExtension` + `ReferralReward` additive columns) — Phase 9.
10. Dropshipping tables (depends only on `VerticalExtension`) — Phase 10, parallelizable with Phase 9 (no shared dependency between the two vertical table sets).

## RLS-on-day-one for every new table

Every migration above includes its RLS policy (`FORCE ROW LEVEL SECURITY`, `USING (tenant_id = current_setting('app.tenant_id')::uuid)`) in the **same migration** that creates the table, for every table above except the two intentionally tenant-independent ones (`IntegrationProviderCatalog`, `DomainDefinition`/`VerticalExtension` — reference data, readable by all tenants, writable only by the platform-admin bypass role). This is the concrete enforcement of KLAROS_FINAL_SECURITY_MODEL.md §D's "new tables RLS-on-day-one" rule — stated here as a migration-authoring requirement, not just a policy aspiration.

## Migration safety (item EE) — current → compatibility → migration → dual-write if required → validation → cutover → deprecation

Applied per major change:

**`Organization.autonomy_level` deprecation:**
Current (inert field, defaulted) → Compatibility layer (none needed — nothing reads it, so there is nothing to keep compatible; it is safe to simply stop writing meaningful values while it still exists as a column) → Migration (none needed now) → Dual-write (not applicable) → Validation (confirm, via the same grep-based check this review used, that no new code introduces a read of it during Phase 0-7 development — a lint rule or CI grep-check is a reasonable low-cost guard) → Cutover (the new Agent autonomy tiers become the actual enforcement point from Phase 7 onward — no "cutover" of existing behavior since none existed) → Deprecation (mark the column deprecated in a docstring/comment update immediately; physical column drop only after one full release cycle post-Phase-7, as a standalone, reversible migration).

**RLS rollout on existing tables:**
Current (app-layer-only filtering) → Compatibility layer (permissive-with-bypass RLS policy, §D of Security Model — behaviorally a no-op, purely additive) → Migration (per-table policy-adding migrations, smallest-blast-radius-first) → Dual-read validation (run the existing app-layer `.where()` filtering **and** the new RLS policy simultaneously during the permissive phase; any row an RLS policy would have blocked but the app-layer query returned anyway is a bug to fix *before* removing the bypass — this is the actual "dual read" check, not a data dual-write) → Cutover (remove bypass per table, by sensitivity) → Deprecation (the manual `.where(tenant_id==...)` convention is **never removed** — it remains as defense-in-depth alongside RLS, not replaced by it; this is intentionally not a full cutover away from the old mechanism).

**Vendor/VendorBill vs. new Dropshipping domain:**
Current (no product/order domain exists) → Compatibility layer (not applicable — genuinely new domain, no existing behavior to preserve) → Migration (new tables only, Phase 10) → Validation (Dropshipping validation scenario, KLAROS_VALIDATION_SCENARIOS.md) → Cutover (not applicable) → Deprecation (not applicable — `Vendor`/`VendorBill` remain unchanged and continue serving their original purpose; the new domain does not touch them).

**General rule applied throughout:** no migration in this plan requires a big-bang cutover with a hard downtime window. Every additive-table change ships independently deployable; the only genuinely staged rollout is RLS itself, which is designed explicitly to avoid big-bang (§D of Security Model, "permissive-with-bypass" phase exists specifically to make this true).

## Rollback strategy per major change

New tables: drop table (pre-production) or feature-flag off the reading code path (post-production) — no existing data is put at risk since nothing existing references the new tables' data as a dependency. Additive columns (`AIInvocationLog.agent_execution_id`, `ReferralReward.currency`/`commission_basis`): nullable, so rollback is simply ceasing to write them; dropping them later is a separate, low-risk migration. RLS policies: droppable per-table without a data migration (§D, Security Model). `Organization.autonomy_level`: no rollback needed since no behavior changes when it's deprecated (nothing currently depends on its enforcement, because it never had any).
