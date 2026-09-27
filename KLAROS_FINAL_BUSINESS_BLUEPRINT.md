# Klaros AI — Canonical Business Blueprint Model

Covers item E. Full entity fields are in KLAROS_FINAL_DOMAIN_MODEL.md (`BusinessBlueprint`/`BlueprintSection`/`BlueprintClaim`); this document validates the structure and settles persistence strategy.

## Validated section structure

The proposed 20 fixed section keys (Identity, Goals, Requirements, Customers, Products/Services, Markets, Providers, Integrations, Data, Agents, Workflows, Website, Policies, Automations, Knowledge, Launch-configuration, plus vertical-specific extensions) are **kept as-is** — they map cleanly onto the subsystems this whole document set defines (Agents section ↔ `Agent` entities, Integrations section ↔ `IntegrationConnection`/catalog, Website section ↔ `Website` entities, etc.), meaning the Blueprint is genuinely the index into everything else rather than a parallel, disconnected description of it.

## What belongs in the Blueprint vs. stays separate

| Concept | Belongs in Blueprint? | Where it actually lives |
|---|---|---|
| "We operate in the home-renovation-financing vertical" | Yes — `Identity`/`Requirements` sections reference the enabled `VerticalExtension` | `BlueprintSection` (JSONB) + `OrganizationVerticalExtension` join row |
| "Our agent for lead qualification is configured to X" | Yes, as a **reference** (agent id + summary), not the full config | `BlueprintSection.Agents` holds a pointer; the actual `Agent`/`AgentVersion` rows are the source of truth |
| Runtime state of a specific agent execution | **No** | `AgentExecution` (runtime state table, not Blueprint) |
| "We require QuickBooks integration" | Yes, as a `BlueprintClaim` (type=Requirement) | `BlueprintClaim`, optionally producing a `Recommendation` |
| Actual QuickBooks OAuth token | **No** | `IntegrationConnection` (existing, encrypted) |
| "The business is a dropshipping business using Supplier X" | Yes — `Identity`/`Markets`/`Providers` sections | `BlueprintSection` + vertical extension tables (`Supplier`, etc.) reference each other by id |
| Actual product catalog / SKUs / inventory counts | **No** | Vertical extension tables (`Product`, `SKU`, `InventorySnapshot`) — these are operational data, not specification data, and change far more often than a Blueprint should version |
| "We decided not to support international shipping (assumption confirmed)" | Yes — this is exactly what `BlueprintClaim` (type=Decision/Assumption) exists for | `BlueprintClaim` |

**Governing principle:** the Blueprint holds **specification and decisions**, never **runtime/operational state**. A Blueprint section may *reference* a live entity (an agent, an integration connection, a product table) by id, but never duplicates that entity's mutable operational data inline. This is what prevents the "giant unstructured JSON blob" failure mode structurally, not just by policy: every section's JSONB payload is schema-validated per section, and anything that would need to change on every order or every agent run is explicitly excluded from the schema and lives in its own table instead.

## Versioned / immutable / generated / human-approved / runtime / configuration / derived — classified

- **Versioned:** `BusinessBlueprint` (whole-row versioning on any section edit), `AgentVersion`, `WorkflowVersion`, `WebsiteVersion` — all follow the same pattern (immutable snapshot + monotonic version number + explicit publish/activate step), deliberately reusing one versioning convention across every new subsystem rather than inventing one per entity type.
- **Immutable once referenced:** an `AgentVersion` that has any `AgentExecution` against it, a `BusinessBlueprint` version once superseded — edits always create a new version, never mutate history. This is required for audit (item C's audit step) to mean anything.
- **Generated:** `Recommendation` rows (engine output), `DiscoverySession` question text (adaptive, engine-generated per turn) — generated content is always attributed a `source` and, where it becomes a durable claim, a `confidence` score; generated content is never silently promoted to CONFIRMED status without the human-approval step below.
- **Human-approved:** `BlueprintClaim.status` transitions from PROPOSED to CONFIRMED only via an explicit tool call (`blueprint.confirm_claim`), gated by `MANAGE_BLUEPRINT`; `WebsiteVersion` publish requires `PublishRequest` (reusing `ApprovalRequest`); `Recommendation.status` moving to ACCEPTED is a human (or, at Execute-autonomous agent tier, a governed-agent) action, always audited.
- **Runtime state:** `AgentExecution`, `DiscoverySession` (until promoted), product/order/inventory operational tables — never part of a Blueprint version.
- **Configuration:** `Agent`/`AgentVersion` full definitions, `IntegrationConnection` settings, `Website`/`WebsiteVersion` full specs — the Blueprint references these by id; it is not their source of truth.
- **Derived data:** UI-rendered integration status taxonomy (KLAROS_FINAL_INTEGRATION_MODEL.md), Recommendation confidence rollups — never persisted as Blueprint content, always computed at read time from the underlying tables.

## Canonical persistence strategy

`BusinessBlueprint` (versioned envelope) → 20 `BlueprintSection` rows (JSONB, per-section-schema-validated, each holding either inline small values or references to other tables' ids) → `BlueprintClaim` rows (the atomic, confidence-scored, evidence-linked facts that sections' JSONB payloads point to for anything non-trivial). This three-layer design is deliberately **not** a single JSON document, because: (1) sections version and validate independently, (2) individual claims are queryable, auditable, and confirmable/rejectable one at a time rather than requiring a whole-document diff to know what changed, and (3) a vertical extension can add new claim types or new section-schema fields without an ALTER TABLE, satisfying the "avoid a migration per vertical nuance" requirement while still avoiding the "giant unstructured blob" failure mode — the section/claim *boundary* is fixed and relational; only the content *within* it is flexible JSON.

**Completion criteria (used by Discovery to know when a Blueprint can move DRAFT→ACTIVE):** every section tagged `min_bar_required` in its schema has at least one CONFIRMED claim covering its required fields, and no `Unknown`-typed claim exists for a capability-critical field (e.g. compliance/licensing for a regulated vertical) — matching KLAROS_BUSINESS_DISCOVERY_SPEC.md's rule that capability-critical Unknowns are never silently defaulted.
