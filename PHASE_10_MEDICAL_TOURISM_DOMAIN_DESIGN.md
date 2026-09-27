# Phase 10 — Medical Tourism Domain Design

Status: implemented. This document is the Phase 1 ("domain model design")
deliverable the Phase 10 instructions require before any table creation,
written against the repository's own pre-existing, already-validated
design work (`KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md` §3,
`KLAROS_MEDICAL_TOURISM_VALIDATION.md`) rather than re-deriving the model
from scratch — those two documents are explicitly a "design proposal, no
models were created" / "conceptual validation, nothing was implemented"
pair; this phase is the first to actually build the table family they
already specified as the minimum normalized model.

## 1. Canonical entity

`Provider` — the hospital/clinic directory entity. Everything else in the
domain (credentials, offerings, consultations, referral commissions) is
either a child of `Provider` or a one-to-one extension of an existing core
entity that references `Provider`.

## 2. Global reference data vs. tenant-owned

**Global reference data (unchanged by this phase):** `VerticalExtension`
(the `medical_tourism` registry row, promoted from BETA/empty-capabilities
to ACTIVE/populated-capabilities by this phase's migration, but the table
itself remains tenant-independent, exactly as Phase 1 designed it).

**Tenant-owned (all seven new tables):** `Provider`, `ProviderCredential`,
`Procedure`, `ProviderProcedure`, `PatientLead`, `Consultation`,
`ReferralCommission`. A Medical Tourism provider/procedure directory is
inherently each tenant's own curated business data (their partner
hospitals, their procedure catalog, their negotiated prices) — never a
platform-curated catalog like `IntegrationProviderCatalog`. This is why
the same provider name can legitimately exist independently across two
different tenants (proven in
`tests/test_medical_tourism_domain.py::test_same_provider_name_can_exist_independently_across_tenants`).

## 3. Many-to-many relationships

`ProviderProcedure` is the one genuine many-to-many join: a provider can
offer many procedures, a procedure can be offered by many providers, each
pairing carrying its own estimated price/currency. Every other
relationship in the domain is a plain one-to-many or one-to-one FK
(`ProviderCredential.provider_id`, `PatientLead.lead_id`,
`Consultation.appointment_id`/`provider_id`,
`ReferralCommission.referral_id`/`provider_id`).

## 4. Provider-inventory entities

`ProviderProcedure` is the provider-inventory entity (HARD SCOPE's
"provider offerings") — it is the row a patient-facing "available
providers for procedure X, at price Y" query reads.

## 5. Generic Blueprint vs. vertical domain tables

Nothing about Medical Tourism required a new field on `BusinessBlueprint`,
`BlueprintSection`, or `BlueprintClaim`. The existing architecture already
carries everything needed:

- **Vertical selection**: `BusinessBlueprint.vertical_extension_id` (FK to
  `VerticalExtension`, added in Phase 2) — a tenant "selects"
  `medical_tourism` by creating an `OrganizationVerticalExtension` row and
  (optionally) pointing their blueprint at the same
  `VerticalExtension.id`.
- **Required capabilities**: the fixed `BlueprintSectionKey.
  REQUIRED_CAPABILITIES` section's `BlueprintClaim` rows carry capability
  keys as free-form claim values (e.g.
  `"medical_tourism.provider_directory"`) — no new section, no new claim
  type.
- **Vertical-specific elaboration**: would live inside a section's JSONB
  `data` (e.g. `PRODUCTS_SERVICES`/`SUPPLIERS_PROVIDERS`), per
  `KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md` §2 rule 3 — this phase did not
  need to populate any such sub-schema to satisfy the validated
  walkthrough, so none was added (see §9, deferred).
- **Actual domain data** (the providers/procedures/offerings themselves)
  lives entirely in the seven new tables — never inside Blueprint JSON.
  A `BlueprintClaim` says "this business needs a provider directory"; the
  `medical_tourism_providers` table IS that directory.

This is why **zero columns were added to `BusinessBlueprint`,
`BlueprintSection`, or `BlueprintClaim`** — proven by
`tests/test_medical_tourism_no_core_pollution.py::test_business_blueprint_core_table_gained_zero_new_columns`.

## 6. Configuration vs. operational data

- **Configuration**: the `VerticalExtension.capabilities` list (platform-
  curated, one row, read by every tenant) and, if a tenant ever needs
  per-tenant vertical config, `VerticalExtension.configuration_schema`
  (unused by this phase — no Medical Tourism tenant configuration turned
  out to be genuinely needed beyond enabling the vertical itself).
- **Operational**: every row in the seven new tables — providers,
  credentials, procedures, offerings, patient-lead extensions,
  consultations, referral commissions. All tenant-owned, all mutated
  through normal CRUD via the service/API/tool layers.

## 7. Concepts required by the validated workflow

Directly required by `KLAROS_MEDICAL_TOURISM_VALIDATION.md`'s walkthrough
table and confirmed present:

- Provider directory (`Provider`) — row 13 ("Klaros-native").
- Compliance/licensing verification (`ProviderCredential`) — row 11's
  "Do you need to verify each provider's medical licensing/accreditation"
  question.
- Procedure catalog + provider offerings (`Procedure`, `ProviderProcedure`)
  — rows 15/13's `Provider`/`ProviderProcedure` component data.
- Patient lead intake (`PatientLead extends Lead`) — row 16.
- Provider-matching agent read surface (`medical_tourism.search_providers`
  /`search_procedures`/`list_provider_offerings` tools) — row 17's
  "Provider Matching" agent.
- Cross-border commission with currency (`ReferralCommission extends
  Referral`, + `ReferralReward.currency`/`commission_basis`) — the data-
  model validation section's explicitly identified gap.
- Consultation scheduling (`Consultation extends Appointment`) — implied
  by row 17's "schedule a consultation reminder" and the extension spec's
  §3 table.

## 8. Deferred concepts (explicitly out of this phase)

Matching `KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md` §3's own "rejected from the
minimum set" list, unchanged by this phase:

- **`Destination` as its own table** — folded into `Provider.country`/
  `Provider.city`. Sufficient for the validated two-country case;
  promotable later, additively, without breaking anything.
- **`Practitioner` as a separate entity** — folded into
  `Provider.practitioner_name`.
- **`TreatmentPackage`** — not built; the existing `Quote`/
  `QuoteLineItem` model is the documented reuse path if/when needed, not
  exercised by this phase since no test scenario required it.
- **Website Builder integration** (`ProviderDirectoryCard` component,
  public pages) — explicitly MUST-NOT-BUILD for this phase.
- **Provider-matching agent's actual matching algorithm / auto-notify
  workflow** (row 17/18 of the validation walkthrough) — out of HARD
  SCOPE ("no agent swarms", "no replacement workflow engine"); this phase
  only proves the Agent Runtime CAN call a permitted medical_tourism tool
  (Phase 10 §13/validation scenario), not that a matching agent has been
  built and deployed.
- **A Blueprint section sub-schema** for Medical-Tourism-specific
  elaboration (`KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md` §2 rule 3) — not
  needed by the validated scenario; deferred until a real capability
  genuinely requires structured vertical config beyond "vertical enabled +
  required-capability claims".

## 9. API implications

Explicit, typed routes under `/api/v1/medical-tourism/*` — never a
generic `/entity/{type}` endpoint (see `app/api/v1/medical_tourism.py`):
providers (list/get/create + nested credentials list/add/verify),
procedures (list/get/create), offerings (list/create). Mutating calls
route through `ToolRegistry.execute()`; read/list calls query the service
layer directly — the same split this codebase's existing
`retention_referrals.py`/`customers.py` routers already use.

## 10. RBAC implications

Two new permissions (`READ_MEDICAL_TOURISM`, `MANAGE_MEDICAL_TOURISM`),
following the exact read/manage split every other domain in
`app/models/rbac.py` already uses. Grants: OWNER/ADMIN (via
`_ALL_PERMISSIONS`)/MANAGER get both; STAFF/READ_ONLY get read-only;
TECHNICIAN/ACCOUNTANT get neither (out of their domain). See
`tests/test_medical_tourism_domain.py::test_role_permission_matrix_for_medical_tourism`.

## Would this require a generic-core change to work? No.

No generic Klaros core table required a vertical-specific column. The one
touch to an existing table outside the new domain (`ReferralReward.
currency`/`commission_basis`) is additive, nullable, and generically named
(not `medical_tourism_currency`) — pre-authorized by
`KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md` §7 as "the only touch to an existing
table across both extensions," and proven to be the *only* such touch by
`tests/test_medical_tourism_no_core_pollution.py`.
