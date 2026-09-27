"""Single source of truth for the `VerticalExtension` seed data — imported
by both alembic/versions/0041_vertical_extension_registry.py and
tests/test_vertical_extension_registry.py, so the migration and the test
suite can never drift from each other.

KLAROS_PHASE_1_IMPLEMENTATION_PLAN.md §1.1: seeded with `medical_tourism`
and `dropshipping` (status=BETA, since their own table families ship in
later phases — KLAROS_FINAL_DOMAIN_MODEL.md's Medical Tourism/Dropshipping
sections) so the registry mechanism is exercisable/testable now.

Phase 10 (PHASE_10_MEDICAL_TOURISM_DOMAIN_DESIGN.md): `medical_tourism`'s
table family now ships (app/models/medical_tourism.py,
alembic/versions/0049_medical_tourism_domain.py), so this row is promoted
here to `status=ACTIVE`, `version="1.0.0"`, with a real `capabilities`
list — the exact strings the Recommendation Engine
(app/services/recommendation_service.py) and the Tool Catalog keyword
matcher (app/services/tool_catalog_service.py) read generically, with zero
vertical-name branching in either. 0049's own data migration applies this
same dict's values as an UPDATE against the row 0041 already inserted —
this file remains the single source of truth for both migrations plus the
test suite. `dropshipping` is untouched (out of this phase's HARD SCOPE).
"""

import uuid

from app.models.vertical_extension import VerticalExtensionStatus

MEDICAL_TOURISM_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
DROPSHIPPING_ID = uuid.UUID("00000000-0000-4000-8000-000000000002")

SEED_VERTICALS: list[dict] = [
    {
        "id": MEDICAL_TOURISM_ID,
        "key": "medical_tourism",
        "name": "Medical Tourism",
        "description": (
            "Cross-border patient leads, provider/procedure directory, and referral "
            "commissions. Provider/procedure/offering domain, patient-lead intake, "
            "consultations, and cross-border referral commissions — see "
            "PHASE_10_MEDICAL_TOURISM_DOMAIN_DESIGN.md."
        ),
        "version": "1.0.0",
        "status": VerticalExtensionStatus.ACTIVE,
        "capabilities": [
            "medical_tourism.provider_directory",
            "medical_tourism.procedure_catalog",
            "medical_tourism.provider_offerings",
            "medical_tourism.compliance_verification",
            "medical_tourism.patient_leads",
            "medical_tourism.cross_border_commission",
            "medical_tourism.multi_currency",
        ],
        "configuration_schema": None,
        "extra_metadata": None,
    },
    {
        "id": DROPSHIPPING_ID,
        "key": "dropshipping",
        "name": "Dropshipping",
        "description": (
            "Supplier/product/order/fulfillment domain. Table family ships in a later "
            "phase; this row exists so the registry mechanism can be exercised now."
        ),
        "version": "0.0.0-registry-only",
        "status": VerticalExtensionStatus.BETA,
        "capabilities": [],
        "configuration_schema": None,
        "extra_metadata": None,
    },
]

EXPECTED_SEED_KEYS = {"medical_tourism", "dropshipping"}
