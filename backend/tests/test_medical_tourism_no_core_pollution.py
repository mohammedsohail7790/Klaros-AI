"""Phase 10 (PHASE_10_MEDICAL_TOURISM_DOMAIN_DESIGN.md), Phase 12 mandate:
a dedicated test proving `BusinessBlueprint`'s core table gained zero new
columns from this phase (the "generic platform stays generic" success
criterion). Also proves the two other most core-adjacent tables
(`Lead`, `Appointment`, `Referral`) gained zero new columns -- only
`ReferralReward` (a `retention.py` table one level removed from the
`Referral`/`Lead`/`Appointment` "extended by" targets themselves) gained
the two additive, generic (non-vertical-named) columns
KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md §7 explicitly pre-authorizes.
"""

from app.models.business_blueprint import BusinessBlueprint
from app.models.crm import Appointment, Lead
from app.models.retention import Referral, ReferralReward

# Exact column sets as of immediately before Phase 10 (captured from
# app/models/business_blueprint.py / app/models/crm.py as they existed at
# the end of Phase 9 -- see git history for app/models/business_blueprint.py,
# app/models/crm.py, unchanged by this phase).
_EXPECTED_BUSINESS_BLUEPRINT_COLUMNS = {
    "id", "created_at", "updated_at", "tenant_id",
    "status", "version", "created_by", "confirmed_at", "vertical_extension_id", "supersedes_id",
}
_EXPECTED_LEAD_COLUMNS = {
    "id", "created_at", "updated_at", "tenant_id",
    "customer_id", "name", "phone", "phone_normalized", "email", "source", "source_detail",
    "campaign_id", "service_requested", "description", "location", "urgency", "estimated_value",
    "status", "lead_score", "score_version", "score_reason", "qualification_status",
    "assigned_user_id", "idempotency_key",
}
_EXPECTED_APPOINTMENT_COLUMNS = {
    "id", "created_at", "updated_at", "tenant_id",
    "lead_id", "customer_id", "assigned_user_id", "title", "service", "location",
    "start_time", "end_time", "status", "notes", "idempotency_key",
    "external_provider", "external_id",
}
_EXPECTED_REFERRAL_COLUMNS = {
    "id", "created_at", "updated_at", "tenant_id",
    "program_id", "referral_code_id", "referrer_customer_id", "lead_id", "referred_customer_id",
    "job_id", "invoice_id", "status", "revenue_amount", "collected_amount", "created_at_referral",
}
# ReferralReward IS the one pre-authorized exception (KLAROS_DOMAIN_
# EXTENSIBILITY_SPEC.md §7: "One additive column each on ReferralReward
# (currency, commission-basis) for Medical Tourism -- the only touch to
# an existing table across both extensions").
_EXPECTED_REFERRAL_REWARD_COLUMNS = {
    "id", "created_at", "updated_at", "tenant_id",
    "referral_id", "customer_id", "amount", "status", "requested_by", "approved_by",
    "currency", "commission_basis",
}


def test_business_blueprint_core_table_gained_zero_new_columns() -> None:
    actual = {c.name for c in BusinessBlueprint.__table__.columns}
    assert actual == _EXPECTED_BUSINESS_BLUEPRINT_COLUMNS, (
        "BusinessBlueprint gained/lost columns -- the Medical Tourism vertical extension "
        "must be additive-only against generic platform core tables, never require a "
        "vertical-specific column on BusinessBlueprint itself"
    )


def test_lead_core_table_gained_zero_new_columns() -> None:
    actual = {c.name for c in Lead.__table__.columns}
    assert actual == _EXPECTED_LEAD_COLUMNS, (
        "Lead gained/lost columns -- PatientLead must remain a one-to-one extension "
        "table (medical_tourism_patient_leads.lead_id -> Lead.id), never a fork or a "
        "column addition on Lead itself"
    )


def test_appointment_core_table_gained_zero_new_columns() -> None:
    actual = {c.name for c in Appointment.__table__.columns}
    assert actual == _EXPECTED_APPOINTMENT_COLUMNS, (
        "Appointment gained/lost columns -- Consultation must remain a one-to-one "
        "extension table (medical_tourism_consultations.appointment_id -> Appointment.id)"
    )


def test_referral_core_table_gained_zero_new_columns() -> None:
    actual = {c.name for c in Referral.__table__.columns}
    assert actual == _EXPECTED_REFERRAL_COLUMNS, (
        "Referral gained/lost columns -- ReferralCommission must remain a one-to-one "
        "extension table (medical_tourism_referral_commissions.referral_id -> Referral.id)"
    )


def test_referral_reward_gained_only_the_two_pre_authorized_generic_columns() -> None:
    actual = {c.name for c in ReferralReward.__table__.columns}
    assert actual == _EXPECTED_REFERRAL_REWARD_COLUMNS
    new_columns = actual - (_EXPECTED_REFERRAL_REWARD_COLUMNS - {"currency", "commission_basis"})
    assert new_columns == {"currency", "commission_basis"}
    # Generic naming, not vertical-specific (e.g. never
    # "medical_tourism_currency") -- any future vertical/feature needing a
    # reward currency reuses these same two columns.
    for name in new_columns:
        assert "medical_tourism" not in name and "medical" not in name
