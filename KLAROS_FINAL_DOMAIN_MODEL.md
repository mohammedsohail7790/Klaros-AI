# Klaros AI — Final Domain / Entity Model

Covers item L (domain extensibility) and item P (per-entity database specification) for every new major entity. Existing entities (`Lead`, `Customer`, `Appointment`, `Vendor`, `VendorBill`, `Invoice`, `Payment`, `ApprovalRequest`, `IntegrationConnection`, `CompanyMemory`, `Organization`, `User`) are unchanged except where an additive column is explicitly noted; full current inventory is in KLAROS_ARCHITECTURE_REVIEW.md §1.

## Core/domain-extension boundary

**Rule (upheld from KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md, verified as consistent, and confirmed non-negotiable):** core services (`crm_tools.py`, `job_tools.py`, `invoice_tools.py`, the Blueprint/Discovery/Recommendation/Agent/Website services) never import, reference, or branch on a specific vertical by name. No `if business_type == "medical_tourism"` anywhere. A vertical plugs in via:
1. A `VerticalExtension` registry row (id, name, capability-contribution list, status).
2. Additive tables that **FK-reference** core tables (`PatientLead.lead_id → Lead.id`), never fork them.
3. An optional Blueprint sub-schema (extra `BlueprintSection` keys scoped to that vertical).
4. New narrowly-scoped `Tool` subclasses registered normally through the existing `ToolRegistry`/factory — no special-cased registration path.
5. Optional new Website component definitions, registered into the same fixed component registry everyone else uses.

Core services discover vertical capability by querying the `VerticalExtension` registry and checking the organization's enabled verticals — never by a compiled-in switch statement. This is the mechanism that makes "no hardcoding" actually true rather than aspirational: the registry is itself a database table, and enabling/disabling a vertical is a data change, not a code deploy.

## New entities (item P)

For each: purpose, tenant-scoped, relationships, indexes/constraints, versioning, audit, migration strategy, rollback.

### `BusinessBlueprint`
- **Purpose:** the canonical, versioned business specification for an organization.
- **Tenant-scoped:** yes, 1:1 with `Organization` (one active blueprint at a time, prior versions retained).
- **Relationships:** has many `BlueprintSection`, has many `BlueprintClaim` (via sections), belongs to `Organization`.
- **Indexes/constraints:** unique `(tenant_id, version)`; partial unique index `(tenant_id) WHERE status='ACTIVE'` enforcing exactly one active blueprint per tenant.
- **Versioning:** full-row versioning — editing creates a new `BusinessBlueprint` row with `version = prior + 1`, never an in-place mutate of an ACTIVE version.
- **Audit:** every status transition (DRAFT→ACTIVE, ACTIVE→superseded) writes an `AuditLog` entry via the same tool-execution path as any other write (a `blueprint.activate_version` tool, not a raw ORM update from an API handler).
- **Migration:** new table, additive-only, no existing-table changes.
- **Rollback:** dropping the table (pre-production only) or, post-production, marking all rows `DEPRECATED` and reverting reads to a feature flag that disables blueprint-dependent UI.

### `BlueprintSection`
- **Purpose:** one of 20 fixed JSONB sections (Identity, Goals, Requirements, Customers, Products/Services, Markets, Providers, Integrations, Data, Agents, Workflows, Website, Policies, Automations, Knowledge, Launch-configuration, + vertical-specific extensions).
- **Tenant-scoped:** yes (via parent `BusinessBlueprint`).
- **Relationships:** belongs to `BusinessBlueprint`; sections reference `BlueprintClaim` rows by id within their JSONB payload rather than duplicating claim data.
- **Indexes:** unique `(blueprint_id, section_key)`; GIN index on the JSONB payload for search.
- **Why JSONB per section, not one blob:** each section version-tracks independently and can be validated against a per-section JSON Schema without a database migration when a vertical adds new fields to, e.g., the Providers section — this is the mechanism that avoids "a migration per vertical nuance" while still avoiding "one giant unstructured blob," because the *section boundary* is fixed and relational, only the *within-section* shape is flexible.
- **Versioning:** inherits from parent `BusinessBlueprint` version.
- **Migration:** new table.

### `BlueprintClaim`
- **Purpose:** atomic, auditable fact/inference/assumption/requirement/preference/constraint/decision/unknown extracted during Discovery or entered by a human, each with confidence and evidence.
- **Tenant-scoped:** yes.
- **Relationships:** belongs to `BusinessBlueprint`; optionally references a `DiscoverySession` turn as its evidence source; confirmed claims optionally mirror into `CompanyMemory` (existing table, via the existing propose/confirm service, not a new write path).
- **Fields:** `claim_type` (enum: Fact/Inference/Assumption/Requirement/Preference/Constraint/Decision/Unknown), `content`, `confidence` (0-1), `source` (Discovery/Human/Recommendation), `evidence_ref`, `status` (PROPOSED/CONFIRMED/REJECTED/SUPERSEDED).
- **Indexes:** `(tenant_id, blueprint_id, status)`; `(tenant_id, claim_type)`.
- **Audit:** every confirm/reject is a tool call (`blueprint.confirm_claim` / `blueprint.reject_claim`), audited like any other tool execution — this is why the endpoint shape decision in KLAROS_ARCHITECTURE_RECONCILIATION.md #1 matters: explicit action endpoints map 1:1 to explicit audited tool calls.
- **Migration:** new table.

### `DiscoverySession`
- **Purpose:** transient conversation state for the Business Discovery flow (free-text description → adaptive questions → answers), promoted into `BlueprintClaim` rows on confirmation, not itself part of the durable Blueprint.
- **Tenant-scoped:** yes.
- **Relationships:** belongs to `Organization`; has many `DiscoveryTurn` (question/answer pairs); produces `BlueprintClaim` rows on promotion.
- **Lifecycle:** ACTIVE → COMPLETED (min-bar requirements satisfied or capped question count reached) → PROMOTED.
- **Migration:** new table, ships in Phase 2 independent of the Phase 3 Blueprint tables (see KLAROS_ARCHITECTURE_RECONCILIATION.md #4).

### `Recommendation`
- **Purpose:** a single recommended integration/tool/agent/workflow/provider/SaaS-app/capability/website-component, always carrying WHY/WHAT/DEPENDENCIES/COST-IF-KNOWN/REQUIRED-OR-OPTIONAL/ALTERNATIVES/CONFIDENCE/SOURCE.
- **Tenant-scoped:** yes.
- **Relationships:** references the recommended entity polymorphically via `(recommendation_target_type, recommendation_target_id)`; references the `BlueprintClaim`(s) or `VerticalExtension` rule that produced it as its `source`.
- **Fields:** `why` (text), `what` (text), `dependencies` (array of other recommendation ids or capability keys), `cost_estimate` (nullable structured money+cadence), `required` (bool), `alternatives` (array of target refs), `confidence` (0-1), `source` (enum: DiscoveryInference / VerticalExtensionRule / RecommendationEnginePlugin / Human), `status` (PROPOSED/ACCEPTED/DISMISSED).
- **Why not hardcoded rules:** `source=VerticalExtensionRule` recommendations are produced by a **registered plugin function per `VerticalExtension`**, looked up by the vertical's id at runtime — this is the first-class extension mechanism the task requires; there is no `if medical_tourism:` in the Recommendation Engine itself, only a plugin registry keyed by vertical id, exactly mirroring how `ToolRegistry` looks up tools by name rather than switching on tool type.
- **Migration:** new table.

### `IntegrationProviderCatalog`
- **Purpose:** tenant-independent reference data — every known integration provider, its `implementation_status` (REAL/STUB/WEBHOOK_NORMALIZER, matching verified current reality), category, OAuth/API-key shape, recommended-for-vertical tags.
- **Tenant-scoped:** **no** — this is the one new table that is intentionally global reference data, read by all tenants, written only by platform admins (`MANAGE_INTEGRATIONS_CATALOG`).
- **Relationships:** referenced by `IntegrationConnection` (existing, tenant-scoped) via `provider_key`; referenced by `Recommendation` as a target.
- **Constraint enforced at the service layer, not the DB:** a STUB-status catalog row must never be rendered as CONNECTED even if a tenant's `IntegrationConnection.status` was erroneously set — the UI status taxonomy (KLAROS_FINAL_INTEGRATION_MODEL.md) always intersects catalog `implementation_status` with connection `status`.
- **Migration:** new table, seeded via a data migration reflecting the verified-real/stub/normalizer list in KLAROS_ARCHITECTURE_REVIEW.md §1.

### `Provider` / `Capability` (marketplace-generic, not vertical-specific)
- **Purpose:** `Provider` = any external service-providing entity the platform can recommend or connect to (superset covering both integration providers and, for Medical Tourism, hospital/clinic providers — disambiguated by `provider_kind`). `Capability` = a declared unit of function a Provider, Tool, or Agent contributes, used by the Recommendation Engine to match business needs to available means.
- **Tenant-scoped:** `Capability` definitions no (reference data); `Provider` instances for domain use (e.g. a specific clinic) are tenant-scoped via the vertical extension table that owns them (see Medical Tourism below) — the generic `Provider`/`Capability` tables here are the marketplace-level abstraction, not the vertical's own `Provider` model, which is a distinct, vertical-owned table (see below) to avoid conflating "integration provider" with "medical tourism hospital provider" in one polymorphic mess.

### `Agent`, `AgentVersion`, `AgentExecution`, `AgentToolPermission`
See KLAROS_FINAL_AGENT_MODEL.md for full specification — summarized here for the domain-model inventory:
- `Agent` (tenant-scoped): identity, purpose, instructions, autonomy tier, status, current version pointer.
- `AgentVersion` (tenant-scoped, versioned): immutable snapshot of an agent's configuration (instructions, tool list, limits) at a point in time — every execution references the exact `AgentVersion` it ran under, for reproducibility/audit.
- `AgentExecution` (tenant-scoped): one run of an agent, referencing `AgentVersion`, linking to `AIInvocationLog` rows (additive FK `AIInvocationLog.agent_execution_id`) and `AuditLog`/`ApprovalRequest` rows produced during the run.
- `AgentToolPermission` (tenant-scoped): join table, `agent_id` × allowed `tool_name`, optionally further restricted (e.g. read-only subset of a tool's actions).

### `Workflow`, `WorkflowVersion`
- **Purpose:** a business-requirement-derived automation spec (trigger → condition → agent/tool/action steps → approval/retry/timeout/compensation), compiled into either the existing deterministic Automation Engine or a Temporal workflow definition depending on shape (see item N / KLAROS_FINAL_AGENT_MODEL.md §Workflow Generation) — **not** a new execution engine.
- **Tenant-scoped:** yes.
- **Relationships:** `WorkflowVersion` references the specific `Automation` (existing table) or Temporal workflow-type it compiles to; never itself executes directly.
- **Versioning:** `WorkflowVersion` mirrors the existing `Automation` versioning pattern already implemented in `automations.py` (`/{automation_id}/versions`, `/{automation_id}/publish`) rather than inventing a new versioning convention.
- **Migration:** new table, but its runtime effect is entirely mediated through the existing `Automation`/Temporal tables — this table is a compiler input, not a new runtime.

### `Website`, `WebsiteVersion`
- **Purpose:** published site configuration — pages/sections/content/theme/navigation/SEO/forms, built from the fixed component registry.
- **Tenant-scoped:** yes.
- **Relationships:** `WebsiteVersion` snapshots a full site spec; publish creates a `PublishRequest` reusing `ApprovalRequest`.
- **Migration:** new table family (`Website`, `WebsiteVersion`, `WebsitePage`, `WebsiteSection`, `WebsiteFormBinding`).
- **Rollback:** publishing a prior `WebsiteVersion` is itself the rollback mechanism — no separate rollback table needed.

### `DomainDefinition` (= `VerticalExtension`, same entity, name reconciled)
- **Purpose:** registry row per vertical (Medical Tourism, Dropshipping, future verticals), capability-contribution list, status (ACTIVE/BETA/DISABLED).
- **Tenant-scoped:** no (platform reference data) — but organizations opt in via a join table `OrganizationVerticalExtension (tenant_id, vertical_extension_id, enabled_at)`.
- **Migration:** new table, seeded with `medical_tourism` and `dropshipping` rows.

## Vertical extension tables (item L)

### Medical Tourism (extends `Lead`, `Appointment`, `Referral`/`ReferralReward`)
- `Provider` (vertical-owned, distinct from the marketplace-generic `Provider` above — hospitals/clinics), `ProviderCredential`, `Procedure`, `ProviderProcedure` (join), `PatientLead` (FK `lead_id → Lead.id`, adds medical-specific fields — country, procedure interest, urgency — without forking `Lead`), `Consultation` (FK `appointment_id → Appointment.id`), `ReferralCommission` (extends the existing `Referral`; **requires one additive column on `ReferralReward`: `currency` and `commission_basis`**, confirmed necessary because `ReferralReward` today has no currency field — `retention.py:273-325`, directly verified).
- All FK-reference core tables; none fork them.

### Dropshipping (net-new relational domain — confirmed `Vendor`/`VendorBill` insufficient)
- `Supplier`, `Product`, `SKU`, `InventorySnapshot`, `Order`, `OrderLine`, `SupplierOrder`, `Shipment`, `OrderReturn`.
- **Explicitly does not reuse `Vendor`/`VendorBill`** — verified `Vendor` has 4 business columns and `VendorBill` has 5 (`finance.py:236-261`), neither has any concept of product/catalog/SKU/inventory. Building this domain on top of `Vendor` would mean bolting an entire e-commerce data model onto a table meant for "who we pay for services," which is exactly the kind of forced reuse this review's mandate warns against.
- Cart is deliberately **not persisted** — ephemeral, client-side, matching the existing codebase's pattern of no backend-draft-concept for similar transient state.

## Additive-only changes to existing tables (confirmed minimal — 2 total)
1. `AIInvocationLog.agent_execution_id` (nullable FK) — links an existing LLM-call-trail row to the new `AgentExecution` it belongs to, for agent-runtime observability. Nullable so all existing non-agent invocations remain valid.
2. `ReferralReward.currency`, `ReferralReward.commission_basis` — required by Medical Tourism's `ReferralCommission` extension; nullable, backward compatible with existing referral rows (which default to the org's base currency at read time if null).

No other existing table requires modification. Every other new capability is additive-table-only, consistent with the "must NOT be duplicated" list (Customer/Lead, Vendor/VendorBill, Appointment, ApprovalRequest, Invoice/Payment, IntegrationConnection, CompanyMemory) carried forward unchanged from KLAROS_DATABASE_EVOLUTION_PLAN.md and re-verified in this review.

See KLAROS_FINAL_DATABASE_ARCHITECTURE.md for migration sequencing, RLS-on-day-one policy for all of the above, and rollback mechanics in aggregate.
