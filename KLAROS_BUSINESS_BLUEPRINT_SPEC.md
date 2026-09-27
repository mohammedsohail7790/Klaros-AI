# Klaros — Business Blueprint Specification

Status: design proposal. No schema was created against the live database. See `KLAROS_TARGET_ARCHITECTURE.md` §6 and `KLAROS_DATABASE_EVOLUTION_PLAN.md` for how this fits the evolution plan.

## 1. Purpose

The Business Blueprint is the single canonical, queryable representation of "what this tenant's business is" — the thing Discovery produces, Recommendations read, Website Builder renders from, and Agents use as scoped context. It replaces nothing (`Organization` remains the tenant/billing root; `BusinessBlueprint` is a 1:1 child of it) and is additive to every existing table.

## 2. Persisted vs. derived

**Persisted in PostgreSQL** (survives independently, editable, versioned): Identity, Industry, Business model, Customers (segment description, not `Customer` rows themselves), Products/services, Suppliers/providers, Geography, Channels, Revenue model, Customer journey (stage list), Operations (workflow description), Finance (model/terms), Marketing (channel mix), Communications (requirements), Compliance (requirements), Required capabilities, Constraints, Assumptions, Decisions, Goals, KPIs (targets, not live values).

**Derived at read time, never stored redundantly**: Recommended applications (computed by the Recommendation Engine from the persisted fields, re-derivable any time the Blueprint changes — storing a stale derived list would drift), Connected applications (read live from `IntegrationConnection`, not duplicated), Agents (read live from `Agent` table, FK'd to the Blueprint via `AgentContext`, not embedded), Workflows (read live from `Automation`, same reasoning), Website (read live from `Site`/`SiteVersion`, same reasoning), Data model (this *is* the schema itself — not a field on the Blueprint).

Rationale: anything that already has its own first-class table (agents, integrations, sites, workflows) is referenced by FK, never re-embedded as JSON on the Blueprint — this is the same discipline the existing codebase already applies (e.g. `Appointment.external_id` references Google Calendar's event rather than caching its full state).

## 3. Entity model

```
BusinessBlueprint
  id, tenant_id (FK Organization, unique — one blueprint per tenant), status
    (DRAFT/ACTIVE/SUPERSEDED), version, created_by, confirmed_at

BlueprintSection (one row per top-level section below; enables partial completion,
  section-level versioning, and the Guided/Business-Map dual-UI requirement in
  KLAROS_TARGET_ARCHITECTURE.md §12 without needing 24 separate tables)
  id, blueprint_id, section_key (enum, see §4), status (EMPTY/DRAFT/COMPLETE),
  data (JSONB, section-specific shape defined per section_key — see §4),
  updated_at, updated_by

BlueprintFact / BlueprintInference / BlueprintAssumption / BlueprintRequirement /
BlueprintPreference / BlueprintConstraint / BlueprintDecision / BlueprintUnknown
  (all share one physical table BlueprintClaim, discriminated by claim_type — see
  KLAROS_BUSINESS_DISCOVERY_SPEC.md §2 for the full field list and why these are
  atomic rows rather than embedded in BlueprintSection.data)
  id, blueprint_id, section_key, claim_type, key, value (JSONB), confidence,
  source (USER_STATED/AI_INFERRED/SYSTEM_DEFAULT), evidence (text — what in the
  user's discovery conversation produced this), status (PROPOSED/CONFIRMED/REJECTED),
  confirmed_by, confirmed_at
```

Why `BlueprintSection.data` is JSONB rather than 24 fully-normalized tables: the 24 sections in the brief (Identity, Industry, Business model, Customers, Products/services, Suppliers/providers, Geography, Channels, Revenue, Customer journey, Operations, Finance, Marketing, Communications, Compliance, Required capabilities, Constraints, Assumptions, Decisions, Goals) have heterogeneous, evolving shapes across verticals (a Medical Tourism "Products/services" section looks nothing like a Dropshipping one) — forcing them into fixed relational columns today would require a schema migration for every future vertical nuance, defeating `KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md`'s goal. The `BlueprintClaim` table is where individual, addressable, confidence-tracked facts live (needed for the Discovery Engine's Facts/Inferences/Requirements distinction) — `BlueprintSection.data` is the resolved, human-readable summary a UI renders directly.

## 4. Section keys (fixed enum, JSONB shape documented per key, not enforced by DB constraint — validated at the service layer via Pydantic per `section_key`)

`IDENTITY, INDUSTRY, BUSINESS_MODEL, CUSTOMERS, PRODUCTS_SERVICES, SUPPLIERS_PROVIDERS, GEOGRAPHY, CHANNELS, REVENUE, CUSTOMER_JOURNEY, OPERATIONS, FINANCE, MARKETING, COMMUNICATIONS, COMPLIANCE, REQUIRED_CAPABILITIES, CONSTRAINTS, ASSUMPTIONS, DECISIONS, GOALS`

Each section's Pydantic schema is versioned (`schema_version` field inside `data`) so a vertical extension (`KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md`) can register an additional, optional sub-shape for e.g. `PRODUCTS_SERVICES` without a migration — extension packages ship their own Pydantic model that validates a `vertical_extension` key inside the JSONB, core code never needs to know Medical Tourism or Dropshipping exist.

## 5. Required capabilities → Recommendation Engine contract

`REQUIRED_CAPABILITIES` section data is a list of capability keys (e.g. `"payments"`, `"scheduling"`, `"sms_messaging"`, `"provider_directory"`, `"product_catalog"`) — a fixed, extensible vocabulary (new capability keys are additive strings, not enum migrations) that is the single input the Recommendation Engine (`KLAROS_INTEGRATION_MARKETPLACE_SPEC.md`) consumes to match against Integration Marketplace provider `capabilities` metadata and existing Klaros feature areas. This keeps the Blueprint→Recommendation boundary a simple set-intersection, not a second AI call per recommendation.

## 6. Lifecycle

`DRAFT` (Discovery in progress, sections partially filled) → `ACTIVE` (user has confirmed enough sections to proceed — minimum bar: IDENTITY, INDUSTRY, BUSINESS_MODEL, REQUIRED_CAPABILITIES all COMPLETE) → edits after `ACTIVE` create a new `version` (previous version rows retained for audit, `SUPERSEDED`) rather than in-place mutation, so Recommendation/Website/Agent history can be traced against the Blueprint state that produced them at the time.

## 7. Tenant isolation

`BusinessBlueprint.tenant_id` — standard `TenantScopedMixin` pattern, same enforcement chain as every existing table (`CurrentUser.tenant_id` from JWT). `BlueprintSection`/`BlueprintClaim` are scoped transitively via `blueprint_id` FK plus a redundant `tenant_id` column (denormalized, matching the existing codebase's convention on child tables like `QuoteLineItem`) to allow direct tenant-filtered queries without a join, and to make an RLS policy (`KLAROS_SECURITY_EVOLUTION_PLAN.md`) straightforward to write on each table independently.

## 8. Consumers

- **Recommendation Engine** reads `REQUIRED_CAPABILITIES` + relevant sections; writes nothing back to Blueprint (recommendations are their own table, see `KLAROS_INTEGRATION_MARKETPLACE_SPEC.md`).
- **Website Builder** reads `IDENTITY`, `PRODUCTS_SERVICES`, `CHANNELS`, `GEOGRAPHY`, `CUSTOMER_JOURNEY` to derive Website Requirements (`KLAROS_WEBSITE_BUILDER_SPEC.md`).
- **Agent Runtime** reads a scoped subset via `AgentContext.blueprint_id` — an agent never gets raw write access to the Blueprint; Blueprint edits always go through the Discovery/Blueprint service's own confirm flow (§Section 9), even when an agent *proposes* a change (e.g. "I noticed pricing changed") — same `PROPOSED→CONFIRMED` gate as `BlueprintClaim`, echoing `CompanyMemory`'s existing `PENDING→ACTIVE` human-confirmation pattern (verified real in `company_memory_service.py`).
- **CompanyMemory**: once a `BlueprintClaim` is `CONFIRMED`, the Blueprint service writes (or updates) a corresponding `CompanyMemory` row (`memory_type=COMPANY_CONTEXT` or `BUSINESS_RULE`, `source=OWNER_EXPLICIT` or `SYSTEM_DERIVED` depending on how the claim was confirmed) so existing AI call sites (Morning Brief, voice receptionist once wired — see audit's noted gap) get Blueprint facts through the memory channel they already consume, without every AI call site needing to learn a new Blueprint API.

## 9. Editing / confirmation flow

Discovery proposes `BlueprintClaim` rows with `status=PROPOSED` and a `confidence` score (see `KLAROS_BUSINESS_DISCOVERY_SPEC.md`). A human confirms (`status=CONFIRMED`) or rejects via a new `PATCH /business-blueprint/claims/{id}` endpoint, RBAC-gated (`MANAGE_BLUEPRINT`, new permission), audited (`AuditLog`, existing, unchanged). Confirming a claim updates the parent `BlueprintSection.data` (recomputed, not hand-edited independently — prevents the section summary and its underlying claims from drifting apart) and, per §8, mirrors into `CompanyMemory`.

## 10. Migration impact

All tables new and additive; zero change to any existing table. `Organization` gains no new column (the relationship is `BusinessBlueprint.tenant_id → Organization.id`, one-directional). Rollback = drop the new tables; no existing data or code path is touched.

## Cross-references

`KLAROS_BUSINESS_DISCOVERY_SPEC.md` (claim types, adaptive questioning), `KLAROS_TARGET_ARCHITECTURE.md` §6–§7, `KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md` (vertical `PRODUCTS_SERVICES`/`SUPPLIERS_PROVIDERS` extensions), `KLAROS_INTEGRATION_MARKETPLACE_SPEC.md`, `KLAROS_DATABASE_EVOLUTION_PLAN.md`.
