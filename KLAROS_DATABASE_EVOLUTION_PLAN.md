# Klaros — Database Evolution Plan

Status: proposal only. No migrations were created; no schema was changed. Baseline: 208 SQLAlchemy model classes across 23 files, 40 Alembic migrations (`KLAROS_DATABASE_CURRENT.md`, verified counts). This document lists table families to preserve/extend/add and explicit non-duplication rules; it does not contain runnable migration code.

## 1. Tables preserved unchanged (schema-wise)

All existing 208 classes across Tenancy/Auth, CRM, Operations, Finance, Quote/Contract, Marketing (18 classes), Retention (18 classes), Compliance, Knowledge, Company Memory, AI governance (`AIInvocationLog`, `ApprovalRequest`, `AuditLog`, `TenantToolPolicy`), Automation, Events, Integration, Voice, Notification, Morning Brief, Communication, Actor. Two exceptions with additive-only column changes, listed in §3.

## 2. New table families

| Family | Tables | Spec |
|---|---|---|
| Business Discovery | `DiscoverySession` | `KLAROS_BUSINESS_DISCOVERY_SPEC.md` §4 |
| Business Blueprint | `BusinessBlueprint`, `BlueprintSection`, `BlueprintClaim` | `KLAROS_BUSINESS_BLUEPRINT_SPEC.md` §3 |
| Recommendation | `Recommendation`, `IntegrationProviderCatalog` | `KLAROS_INTEGRATION_MARKETPLACE_SPEC.md` §2, §5 |
| Agent Runtime | `Agent`, `AgentVersion`, `AgentGoal`, `AgentContext`, `AgentMemory`, `AgentToolPermission`, `AgentWorkflow`, `AgentExecution`, `AgentExecutionStep`, `AgentApproval`, `AgentPolicy`, `ToolMetadata` (risk_tier/mutates_state/financial_risk per tool) | `KLAROS_AI_AGENT_ARCHITECTURE.md` §2 |
| Website Builder | `Site`, `SiteVersion`, `Page`, `PageVersion`, `Section`, `Component`, `Navigation`, `Form`, `SEOMetadata`, `Domain`, `BrandSettings`, `PublishRequest` | `KLAROS_WEBSITE_BUILDER_SPEC.md` §4 |
| Domain: Medical Tourism | `Provider`, `ProviderCredential`, `Procedure`, `ProviderProcedure`, `PatientLead`, `Consultation`, `ReferralCommission` | `KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md` §3 |
| Domain: Dropshipping | `Supplier`, `Product`, `SKU`, `InventorySnapshot`, `Order`, `OrderLine`, `SupplierOrder`, `Shipment`, `OrderReturn` | `KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md` §4 |
| Extensibility registry | `VerticalExtension` | `KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md` §2 |

Every table above carries `TenantScopedMixin` (`tenant_id`, timestamps) per existing convention, except tenant-independent catalog tables (`IntegrationProviderCatalog`, `VerticalExtension`, `ToolMetadata`) which are Klaros-maintained reference data, matching the existing precedent of `DEFAULT_TOOL_POLICIES` being a static, non-tenant-scoped table/dict.

## 3. Additive-only changes to existing tables

| Table | Change | Why | Risk |
|---|---|---|---|
| `AIInvocationLog` | + nullable `agent_execution_id` FK | Correlates AI calls to agent executions (`KLAROS_AI_AGENT_ARCHITECTURE.md` §7) | None — nullable, existing three AI call sites (Morning Brief, lead-qualification, voice) leave it `NULL`, unaffected |
| `ReferralReward` | + nullable `currency` (ISO 4217, default org currency), + nullable `commission_basis` (`FLAT`/`PERCENTAGE`) | Cross-border commission support (`KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md` §3); verified today's model has no currency field at all | None — nullable, existing domestic referral-reward flows unaffected |

No other existing table gains, loses, or renames a column. No existing enum value is removed or renamed (only new enum *values* may be added where noted, e.g. new `Permission` entries, new `EventType` entries — additive to StrEnum, not migrations against data since these are code-level enums per §7 of `KLAROS_DATABASE_CURRENT.md`).

## 4. Tables that must NOT be duplicated

Explicit non-duplication list, cross-referenced to the reuse map in `KLAROS_GAP_ANALYSIS.md` §2:

- **`Customer`/`Lead`** — never forked per-vertical. `PatientLead` and `Order.customer_id` both reference the existing tables.
- **`Vendor`/`VendorBill`** — never repurposed for `Supplier`/`SKU`. Confirmed distinct real-world concepts (subcontractor billing vs. product sourcing) and distinct minimal column sets (verified: `Vendor` has 4 business columns, no product/catalog fields).
- **`Appointment`** — `Consultation` extends it via FK, never a parallel scheduling table.
- **`ApprovalRequest`** — `AgentApproval` and `PublishRequest` wrap/reference it, never reimplement approval-state tracking.
- **`Invoice`/`Payment`** — `Order` triggers these via existing Finance services; no parallel payment-recording table for dropshipping.
- **`IntegrationConnection`** — the marketplace adds a catalog table alongside it, never a second per-tenant connection table.
- **`CompanyMemory`** — Blueprint facts mirror into it (one-directional sync on confirm), never a parallel memory table.

## 5. Relationships (summary ERD notes)

`BusinessBlueprint (1) —— (1) Organization`; `BlueprintSection (N) —— (1) BusinessBlueprint`; `BlueprintClaim (N) —— (1) BusinessBlueprint`; `Agent (N) —— (1) Organization`; `AgentExecution (N) —— (1) Agent`, `(N) —— (0..1) BusinessBlueprint` via `AgentContext`; `Site (1) —— (1) BusinessBlueprint`, `(N) —— (1) Organization`; `PatientLead (1) —— (1) Lead`; `Consultation (1) —— (1) Appointment`; `ReferralCommission (1) —— (1) Referral`; `Order (N) —— (1) Customer`; `OrderLine (N) —— (1) Order`, `(N) —— (1) SKU`; `SKU (N) —— (1) Product`, `(N) —— (1) Supplier`.

## 6. Tenant-scoping requirements

Every new table above is either (a) `TenantScopedMixin`-based with the standard `tenant_id` filter chain, or (b) explicitly tenant-independent reference data (§2 note) never queried without a join through a tenant-scoped table first. No new table may be queryable by `id` alone without an implicit or explicit tenant filter — this is the same rule the existing codebase already (mostly) follows; enforcing it for new tables via RLS from day one (`KLAROS_SECURITY_EVOLUTION_PLAN.md`) avoids the existing app-level-only exposure class growing further.

## 7. Index requirements

Standard: `tenant_id` as the leading column on every composite index for tenant-scoped tables (matching existing convention, e.g. `TenantToolPolicy`'s `("tenant_id", "tool_name")` unique constraint, verified). New: `AgentExecution(tenant_id, agent_id, status)` for execution-list queries; `BlueprintClaim(blueprint_id, section_key, status)` for gap-check queries (`KLAROS_BUSINESS_DISCOVERY_SPEC.md` §3); `Recommendation(tenant_id, blueprint_id, user_approval_state)`; `SiteVersion(site_id, status)` partial-unique to enforce at most one `PUBLISHED` version per site; `SKU(supplier_id)`, `OrderLine(order_id)` for the dropshipping domain's expected query patterns.

## 8. Unique constraints

`BusinessBlueprint(tenant_id)` — one active blueprint per tenant (unique partial index on `status != SUPERSEDED`, allowing history). `Agent(tenant_id, name)`. `Site(tenant_id, blueprint_id)`. `VerticalExtension(key)`. `SiteVersion(site_id)` partial-unique on `status = PUBLISHED` (§7).

## 9. Audit requirements

Every new table that represents a state-changing action (`BlueprintClaim.status` transitions, `Agent`/`AgentVersion` creation, `PublishRequest` decisions, `Recommendation.user_approval_state` changes) writes an `AuditLog` row via the existing, unchanged `AuditLog` model and service call pattern — no new audit table is introduced; this reuses the existing mechanism exactly as `ApprovalRequest` decisions already do.

## 10. Migration dependencies and sequencing

1. `VerticalExtension`, `IntegrationProviderCatalog`, `ToolMetadata` (reference data, no dependencies) — Phase 0/1.
2. `BusinessBlueprint`, `BlueprintSection`, `BlueprintClaim`, `DiscoverySession` (depend on `Organization` only) — Phase 2/3.
3. `Recommendation` (depends on `BusinessBlueprint`, `IntegrationProviderCatalog`) — Phase 4.
4. Agent Runtime tables (depend on `BusinessBlueprint` for `AgentContext`, `ToolMetadata` for policy metadata) — Phase 7, gated by the P0 governance items in `KLAROS_GAP_ANALYSIS.md` §1 being resolved first (autonomy enforcement design must exist before `AgentPolicy`/`AgentToolPermission` schemas are finalized, to avoid a schema rework).
5. Website Builder tables (depend on `BusinessBlueprint`) — Phase 6.
6. Domain extension tables (depend on core CRM/Finance/Retention, `VerticalExtension`) — Phases 9–10, independent of each other, can be built in parallel once the extension pattern (step 1) exists.
7. `ReferralReward` additive columns — bundled with the Medical Tourism migration (Phase 9), not earlier, since nothing needs them until then.

## 11. Migration safety

Every migration in this plan is additive (new table or new nullable column) — none requires a data backfill, a NOT NULL column without a server default, or a rename/drop of existing structure. Per `KLAROS_DEPLOYMENT_EVOLUTION_PLAN.md` §5, each migration must be verified reversible (`alembic downgrade -1`) in CI before merge, and must apply cleanly against a copy of the current 40-migration chain without conflict (sequential numbering continues from wherever the chain stands at implementation time — exact next migration number is **UNKNOWN — REQUIRES VERIFICATION** at implementation time, not fixed by this document).

## Cross-references

`KLAROS_GAP_ANALYSIS.md` §2–§3, `KLAROS_SECURITY_EVOLUTION_PLAN.md` (RLS rollout order), `KLAROS_IMPLEMENTATION_ROADMAP.md` (phase-by-phase sequencing detail).
