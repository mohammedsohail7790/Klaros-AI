"""The explicit eight-stage Medical Tourism patient pipeline.

  1 NEW_ENQUIRY       a lead exists and no consent evidence has been recorded yet
  2 CONSENT           consent evidence exists but personal-data storage is not (or is no longer) granted: pending, declined or withdrawn
  3 QUALIFICATION     personal data may be kept; the enquiry is not yet QUALIFIED (pending, or held for a person)
  4 PROVIDER_OPTIONS  qualified; provider options can be prepared from VERIFIED providers (no referral selected yet)
  5 PATIENT_APPROVAL  a provider has been selected; the patient's decision to share with it is not yet SENT (draft, or approved and awaiting the transfer)
  6 REFERRED          the permitted data was transferred and the clinic has not answered
  7 CLINIC_RESPONSE   the clinic accepted or declined the enquiry
  8 OUTCOME           the clinic reported an outcome

The stage is DERIVED from facts that are recorded elsewhere (consent evidence, qualification, referrals), never typed in, so it cannot drift from the truth and a
stage cannot be skipped: e.g. REFERRED is reachable only through a referral that was actually sent, and OUTCOME only through an outcome the clinic reported. A
consultation request is not a confirmed appointment and a potential commission is not revenue: neither appears here.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.db.session import set_tenant_context
from app.models.crm import Lead, QualificationStatus
from app.models.halla_consent import HallaConsentEvidence
from app.models.medical_tourism_referral import HandoffTask, ProviderReferral, ReferralStatus, TaskStatus
from app.services import halla_consent
from app.models.medical_tourism import PatientLead, Provider, ReferralCommission
from app.services.consent_gate import STORE_MEDICAL, STORE_PERSONAL

STAGES = ("NEW_ENQUIRY", "CONSENT", "QUALIFICATION", "PROVIDER_OPTIONS", "PATIENT_APPROVAL", "REFERRED", "CLINIC_RESPONSE", "OUTCOME")
NEXT_ACTION = {
    "NEW_ENQUIRY": "Record or wait for the patient's consent decision.",
    "CONSENT": "Personal details are not kept. Ask the patient whether they agree, or leave the enquiry as it is.",
    "QUALIFICATION": "Complete the administrative qualification (or review why it is held for a person).",
    "PROVIDER_OPTIONS": "Prepare options from verified providers and ask the patient which clinic they would like.",
    "PATIENT_APPROVAL": "Record the patient's decision to share with the chosen clinic, then send the referral.",
    "REFERRED": "Wait for the clinic's answer; chase it if it is overdue.",
    "CLINIC_RESPONSE": "Record the outcome the clinic reports.",
    "OUTCOME": "Reconcile any commission with the clinic's reported outcome.",
}


@dataclass(frozen=True)
class Facts:
    has_evidence: bool
    personal_ok: bool
    qualification: str
    referral_statuses: tuple[str, ...]
    outcome_reported: bool


def derive_stage(f: Facts) -> str:
    if f.outcome_reported or ReferralStatus.OUTCOME_REPORTED in f.referral_statuses:
        return "OUTCOME"
    if any(s in (ReferralStatus.ACCEPTED, ReferralStatus.DECLINED) for s in f.referral_statuses):
        return "CLINIC_RESPONSE"
    if ReferralStatus.SENT in f.referral_statuses:
        return "REFERRED"
    if any(s in (ReferralStatus.DRAFT, ReferralStatus.APPROVED) for s in f.referral_statuses):
        return "PATIENT_APPROVAL"
    if not f.has_evidence:
        return "NEW_ENQUIRY"
    if not f.personal_ok:
        return "CONSENT"
    if f.qualification != QualificationStatus.QUALIFIED:
        return "QUALIFICATION"
    return "PROVIDER_OPTIONS"


async def build_pipeline(session_factory, tenant_id: uuid.UUID, *, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    """Every lead of the tenant with its derived stage. A lead whose personal data is not consented is listed WITHOUT a name."""
    async with session_factory() as s:
        await set_tenant_context(s, tenant_id)
        leads = list((await s.execute(select(Lead).where(Lead.tenant_id == tenant_id).order_by(Lead.created_at.desc()).limit(limit).offset(offset))).scalars())
        evidence = list((await s.execute(select(HallaConsentEvidence).where(HallaConsentEvidence.tenant_id == tenant_id))).scalars())
        referrals = list((await s.execute(select(ProviderReferral).where(ProviderReferral.tenant_id == tenant_id))).scalars())
        tasks = list((await s.execute(select(HandoffTask).where(HandoffTask.tenant_id == tenant_id, HandoffTask.status.in_((TaskStatus.OPEN, TaskStatus.ACCEPTED))))).scalars())
        intake_rows = {p.lead_id: p for p in (await s.execute(select(PatientLead).where(PatientLead.tenant_id == tenant_id))).scalars()}
        provider_names = {p.id: p.name for p in (await s.execute(select(Provider).where(Provider.tenant_id == tenant_id))).scalars()}
        commission_of = {c.provider_referral_id: c.status for c in (await s.execute(select(ReferralCommission).where(ReferralCommission.tenant_id == tenant_id, ReferralCommission.provider_referral_id.is_not(None)))).scalars()}
    by_lead: dict[uuid.UUID, list] = defaultdict(list)
    by_halla: dict[str, list] = defaultdict(list)
    for r in evidence:
        if r.lead_id is not None:
            by_lead[r.lead_id].append(r)
        if r.halla_lead_id:
            by_halla[r.halla_lead_id].append(r)
    refs: dict[uuid.UUID, list[ProviderReferral]] = defaultdict(list)
    for r in referrals:
        refs[r.lead_id].append(r)
    open_tasks: dict[uuid.UUID, list[HandoffTask]] = defaultdict(list)
    for t in tasks:
        open_tasks[t.lead_id].append(t)

    rows, counts = [], {s: 0 for s in STAGES}
    for lead in leads:
        merged = {r.id: r for r in by_lead.get(lead.id, [])}
        merged.update({r.id: r for r in by_halla.get(lead.external_id or "", [])})
        state = halla_consent.derive_state(merged.values())
        mine = refs.get(lead.id, [])
        stage = derive_stage(Facts(state.has_evidence, state.allows(STORE_PERSONAL), str(lead.qualification_status), tuple(r.status for r in mine), any(r.outcome for r in mine)))
        counts[stage] += 1
        pl = intake_rows.get(lead.id)
        personal_ok = state.allows(STORE_PERSONAL)
        rows.append({
            "lead_id": str(lead.id),
            "name": lead.name if state.allows(STORE_PERSONAL) else None,  # data minimisation: no consent to keep it, no name in a list
            "stage": stage, "stage_number": STAGES.index(stage) + 1, "next_action": NEXT_ACTION[stage],
            "held_for_person": str(lead.qualification_status) == str(QualificationStatus.REQUIRES_HUMAN),
            "consent_frozen": state.has_evidence and not state.allows(STORE_PERSONAL),
            # What the enquiry is about, shown only while the patient's consent allows it (details need store_personal_data, the treatment also needs store_medical_information).
            "intake": {
                "procedure": ((pl.procedure_text if pl is not None else None) or lead.service_requested) if personal_ok and state.allows(STORE_MEDICAL) else None,  # a treatment typed in by hand counts too
                "destination": pl.destination_text if pl is not None and personal_ok else None,
                "timeframe": pl.timeframe if pl is not None and personal_ok else None,
                "language": pl.preferred_language if pl is not None and personal_ok else None,
                "contact_time": pl.preferred_contact_time if pl is not None and personal_ok else None,
            },
            "referrals": [
                {
                    "id": str(r.id), "provider_id": str(r.provider_id), "provider_name": provider_names.get(r.provider_id), "status": r.status,
                    "sent_at": r.sent_at.isoformat() if r.sent_at else None,
                    "clinic_response_at": r.clinic_response_at.isoformat() if r.clinic_response_at else None,
                    "outcome_reported_at": r.outcome_reported_at.isoformat() if r.outcome_reported_at else None,
                    "commission_status": commission_of.get(r.id),  # None until a commission is set up
                }
                for r in mine
            ],
            "open_tasks": len(open_tasks.get(lead.id, [])),
            "assigned_user_id": str(lead.assigned_user_id) if lead.assigned_user_id else None,
            "created_at": lead.created_at.isoformat() if lead.created_at else None,
        })
    return {"stages": list(STAGES), "counts": counts, "leads": rows, "definitions": "Derived from recorded facts; a stage cannot be set by hand or skipped."}
