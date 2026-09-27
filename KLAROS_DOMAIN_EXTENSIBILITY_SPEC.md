# Klaros — Domain Extensibility Specification

Status: design proposal. No models were created. Includes the Medical Tourism and Dropshipping extension designs (brief phases 13–14), generalized into a reusable pattern per the brief's requirement that these two verticals validate, not define, the extensibility mechanism.

## 1. The extensibility problem

Klaros's core (CRM, Jobs, Finance, Marketing, Retention) is verified domain-agnostic (`Lead`/`Customer`/`Appointment` carry no home-services-specific required fields; `KLAROS_AI_CODEBASE_AUDIT.md` §28–§29 independently reaches the same conclusion). The risk `KLAROS_DO_NOT_BUILD_YET.md` §10 flags is building Medical Tourism and Dropshipping support as special-cased branches deep in core services. This document defines the pattern that prevents that, and uses both test businesses as its acceptance tests.

## 2. Extension pattern

A **vertical extension** is:

1. A `VerticalExtension` registry row (new, tiny table: `key`, `display_name`, `required_capabilities` it advertises, `status` ACTIVE/BETA) — not code-generated, hand-registered by Klaros engineers when a new vertical ships, same as adding a new integration provider row.
2. A set of **additive tables** that reference core entities by foreign key, never fork or duplicate them. Rule: if a concept already exists in core (a customer, a lead, a payment, an invoice line), the extension adds columns/child-rows, not a parallel entity. If a concept genuinely doesn't exist in core (a hospital, a SKU), it gets a new table, still tenant-scoped via the standard `TenantScopedMixin` pattern, still referencing `Lead`/`Customer`/`Invoice` where the relationship is real.
3. An optional **Blueprint section sub-schema** (`KLAROS_BUSINESS_BLUEPRINT_SPEC.md` §4) registered against `PRODUCTS_SERVICES`/`SUPPLIERS_PROVIDERS`, validated by the extension's own Pydantic model, read generically by the Blueprint service (which never needs an `if vertical == X` branch — it just validates whatever sub-schema is registered for the tenant's chosen `VerticalExtension`).
4. A set of new, narrowly-scoped **Tools** (registered into the existing `ToolRegistry` via `factory.py`, same mechanism as every existing tool) for vertical-specific actions (e.g. `medical_tourism.match_providers`, `ecommerce.check_inventory`).
5. Optional new **Website components** (`KLAROS_WEBSITE_BUILDER_SPEC.md` §3) registered into the shared component registry.
6. A **capability contribution list** feeding the Recommendation Engine (`KLAROS_BUSINESS_BLUEPRINT_SPEC.md` §5) — e.g. Dropshipping contributes `"product_catalog"`, `"supplier_integration"`, `"order_fulfillment"` as capability keys the Integration Marketplace can match providers against.

Core services (`LeadService`, `InvoiceService`, `ApprovalService`, `ToolRegistry`, `AIExecutionService`, `AgentExecutionService`) never import or reference a specific vertical extension by name. Vertical-specific logic lives entirely inside the extension's own service/tool files, which *depend on* core services (import `LeadService` to create a `Lead`), never the reverse.

## 3. Medical Tourism extension — minimum normalized model

Evaluated against the brief's full candidate list (Provider/Hospital/Clinic/Practitioner/Procedure/Treatment/Destination/ProviderCredential/Accreditation/PatientLead/Referral/ReferralCommission/Consultation/TreatmentPackage/ProviderAvailability) and reduced to the minimum that doesn't duplicate CRM:

| New table | Purpose | FK relationship to core |
|---|---|---|
| `Provider` | Hospital/clinic entity (name, destination country, accreditation refs) | none required — standalone directory entity |
| `ProviderCredential` | Licensing/accreditation records | `provider_id → Provider.id` |
| `Procedure` | Treatment/procedure catalog (name, category, typical destination countries) | none |
| `ProviderProcedure` | Join table: which providers offer which procedures, at what estimated price | `provider_id`, `procedure_id` |
| `PatientLead` | Medical-specific intake fields (procedure of interest, medical history summary, travel dates, insurance) | **`lead_id → Lead.id` (one-to-one extension, not a new lead concept)** |
| `Consultation` | A scheduled provider-patient consultation | `appointment_id → Appointment.id` (extension), `provider_id → Provider.id` |
| `ReferralCommission` | Cross-border commission terms and computed amount **with a currency field** (a gap the verified `Referral`/`ReferralReward` tables lack today — no currency column exists on either) | `referral_id → Referral.id` (extends, not replaces, the existing retention `Referral` model) |

**Rejected from the minimum set**: `Practitioner` as a separate entity from `Provider` (folded into `Provider` with a `practitioner_name` field for v1 — a hospital-level directory is sufficient for the validation case; a full practitioner roster is a P3 refinement if demand emerges), `TreatmentPackage` (represented as a `Quote` with `ProviderProcedure`-linked line items, reusing the existing `Quote`/`QuoteLineItem` model rather than inventing a parallel bundling concept), `Destination` as its own table (folded into `Provider.country`/`Provider.city` — a full geography taxonomy is unnecessary for two countries in the validation case and can be added additively later without breaking anything).

**Connection to existing systems**: `PatientLead` extends `Lead` (qualification, scoring, conversion all reuse `LeadService` unchanged); `Consultation` extends `Appointment` (Google Calendar sync reused unchanged); `ReferralCommission` extends the existing `Referral`/`ReferralReward` retention models (reuses the existing `PENDING→APPROVED→ISSUED` reward lifecycle, adding currency and a percentage-basis calculation field via a new column on `ReferralReward` — additive migration, not a new payout system); Finance is untouched — a paid commission still becomes a normal `Invoice`/`Payment` or `Payout` row via existing services, with `ReferralCommission` only supplying the computed amount.

## 4. Dropshipping extension — minimum normalized model

Evaluated against Product/SKU/Supplier/Catalog/Inventory/Price/Customer/Cart/Order/OrderLine/Payment/Fulfillment/Shipment/Return/Refund/SupplierOrder. **Critical constraint (explicitly verified)**: `Vendor`/`VendorBill` (`backend/app/models/finance.py:236-261`) have exactly 4 and 5 business columns respectively (name/email/phone/status; vendor_id/job_id/amount/due_date/status/external_reference) — no product/SKU/catalog concept whatsoever. They model subcontractor bill payment, not e-commerce supply. **They must not be reused for this extension.**

| New table | Purpose | FK relationship to core |
|---|---|---|
| `Supplier` | Dropship supplier entity (name, integration type, contact) — **separate from `Vendor`** | none required |
| `Product` | Catalog product (name, description, category, images via object storage) | none |
| `SKU` | Sellable variant (price, weight, supplier reference, supplier SKU code) | `product_id → Product.id`, `supplier_id → Supplier.id` |
| `InventorySnapshot` | Point-in-time stock level per SKU (dropshipping is typically supplier-fulfilled with no owned inventory, so this is often a cached supplier-reported count, not a warehouse ledger) | `sku_id → SKU.id` |
| `Order` | Customer order | `customer_id → Customer.id` (reuses core `Customer`, not a new entity) |
| `OrderLine` | Line item | `order_id → Order.id`, `sku_id → SKU.id` |
| `SupplierOrder` | The corresponding order placed to the supplier for fulfillment | `order_id → Order.id`, `supplier_id → Supplier.id` |
| `Shipment` | Tracking info | `supplier_order_id → SupplierOrder.id` |
| `OrderReturn` | Return/refund request | `order_id → Order.id`; approval reuses existing `ApprovalRequest` pattern |

**Connection to existing systems**: `Order`/`OrderLine` are new (no existing "order" concept in Klaros to extend — the audit confirms zero product/order models exist), but `Order.customer_id` reuses core `Customer`; payment for an `Order` creates a normal `Invoice`+`Payment` via existing Finance services (an `Order` is not itself a payment record — it triggers one, same relationship pattern `Job`→`Invoice` already has); marketing/analytics reuse the existing generic Marketing suite unchanged (a `Campaign` doesn't need to know whether it's promoting jobs or products). `Cart` is deliberately **not** a backend entity — pre-checkout cart state is ephemeral client-side/session state (matching how the existing frontend has no backend "draft" concept for anything else either), only becoming an `Order` row at checkout submission.

## 5. Recommendation Engine validation (both verticals)

Medical Tourism Blueprint → `REQUIRED_CAPABILITIES` includes `provider_directory`, `cross_border_commission`, `multi_currency` → Recommendation Engine matches zero existing `IntegrationProviderCatalog` entries for `provider_directory` (correctly surfaces this as a Klaros-native new feature, not an integration gap) and matches `stripe`/`quickbooks` for payment/accounting needs (existing, real). Dropshipping Blueprint → `REQUIRED_CAPABILITIES` includes `supplier_integration`, `product_catalog`, `order_fulfillment`, `payment_processing` → matches `stripe` (real) for payments and correctly surfaces that no generic "supplier API" integration exists today (a `Supplier` connection is typically a bespoke per-supplier integration, out of scope for a generic marketplace entry — flagged as `CUSTOM` status per `KLAROS_INTEGRATION_MARKETPLACE_SPEC.md` §4). Full walkthroughs in `KLAROS_MEDICAL_TOURISM_VALIDATION.md` and `KLAROS_DROPSHIPPING_VALIDATION.md`.

## 6. Future third vertical (extensibility proof, not built)

To confirm the pattern generalizes: a hypothetical "Home Renovation Financing" vertical would register a `VerticalExtension` row, add a `FinancingApplication` table (FK to existing `Job`/`Customer`), contribute `"financing_integration"` to `REQUIRED_CAPABILITIES`, and require zero changes to `LeadService`, `ToolRegistry`, `AgentExecutionService`, or `Recommendation` matching logic — only new rows/tables/tools, exactly like Medical Tourism and Dropshipping above. This is the acceptance bar for "does the pattern actually generalize," per the brief's requirement not to hard-code around the two test businesses.

## 7. Migration impact

All new tables per vertical, additive only. One additive column each on `ReferralReward` (currency, commission-basis) for Medical Tourism — the only touch to an existing table across both extensions. No existing table is forked, renamed, or has its meaning changed.

## Cross-references

`KLAROS_MEDICAL_TOURISM_VALIDATION.md`, `KLAROS_DROPSHIPPING_VALIDATION.md`, `KLAROS_BUSINESS_BLUEPRINT_SPEC.md` §4, `KLAROS_INTEGRATION_MARKETPLACE_SPEC.md` §5, `KLAROS_ARCHITECTURAL_DECISIONS.md` ADR-007, `KLAROS_DO_NOT_BUILD_YET.md` §8, §10.
