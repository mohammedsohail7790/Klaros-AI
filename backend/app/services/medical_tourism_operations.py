"""Medical Tourism: the operational layer on top of the existing domain models —
provider matching for a lead, a lead's operating context (timeline + next action),
and the metrics this module contributes to the Business Operations console.

Nothing here adds a table or a second source of truth: every figure and every match
is derived from the existing Provider / Procedure / ProviderProcedure /
ProviderCredential / PatientLead / Consultation / Appointment / AuditLog rows.
Matching is deterministic and explains itself; it never invents a provider
capability and never sends, books or contacts anything.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from typing import Any

from sqlalchemy import func, select

from app.db.session import async_session_maker, set_tenant_context
from app.models.audit_log import AuditLog
from app.models.crm import Appointment, Lead
from app.models.medical_tourism import (
    Consultation,
    CredentialStatus,
    PatientLead,
    Procedure,
    ProcedureStatus,
    Provider,
    ProviderCredential,
    ProviderProcedure,
    ProviderProcedureStatus,
    ProviderStatus,
    ReferralCommission,
)
from app.services.operations_providers import register_operations_provider

_STOP = {"the", "and", "for", "with", "from", "need", "want", "would", "like", "please", "about", "have", "some", "that", "this", "your", "interested"}


def _tokens(text: str | None) -> set[str]:
    return {t for t in re.findall(r"[a-z]{4,}", (text or "").lower()) if t not in _STOP}


def _humanize_source(source: str) -> str:
    return {"WEB": "your website", "CHAT": "website chat", "PHONE": "a phone call"}.get(source, source.replace("_", " ").lower())


async def match_providers(tenant_id: uuid.UUID, lead_id: uuid.UUID) -> dict[str, Any] | None:
    """Suggest configured providers for a patient lead. Returns None when the lead has
    no patient context (it is not a patient lead)."""
    async with async_session_maker() as session:
        await set_tenant_context(session, tenant_id)
        lead = (await session.execute(select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id))).scalar_one_or_none()
        patient = (
            await session.execute(select(PatientLead).where(PatientLead.lead_id == lead_id, PatientLead.tenant_id == tenant_id))
        ).scalar_one_or_none()
        if lead is None or patient is None:
            return None

        procedures = (
            await session.execute(select(Procedure).where(Procedure.tenant_id == tenant_id, Procedure.status == ProcedureStatus.ACTIVE))
        ).scalars().all()
        by_id = {p.id: p for p in procedures}

        target: list[Procedure] = []
        basis_source: str | None = None
        if patient.procedure_id and patient.procedure_id in by_id:
            target, basis_source = [by_id[patient.procedure_id]], "stated"
        else:
            words = _tokens(lead.service_requested) | _tokens(lead.description) | _tokens(patient.medical_history_summary)
            for p in procedures:
                if _tokens(p.name) & words:
                    target.append(p)
            if target:
                basis_source = "inferred from the enquiry text"

        providers = (
            await session.execute(select(Provider).where(Provider.tenant_id == tenant_id, Provider.status == ProviderStatus.ACTIVE))
        ).scalars().all()
        offerings = (
            await session.execute(
                select(ProviderProcedure).where(
                    ProviderProcedure.tenant_id == tenant_id, ProviderProcedure.status == ProviderProcedureStatus.ACTIVE
                )
            )
        ).scalars().all()
        verified = dict(
            (
                await session.execute(
                    select(ProviderCredential.provider_id, func.count())
                    .where(ProviderCredential.tenant_id == tenant_id, ProviderCredential.status == CredentialStatus.VERIFIED)
                    .group_by(ProviderCredential.provider_id)
                )
            ).all()
        )

    country = patient.preferred_destination_country
    target_ids = {p.id for p in target}
    matches: list[dict[str, Any]] = []
    for prov in providers:
        mine = [o for o in offerings if o.provider_id == prov.id and o.procedure_id in target_ids]
        in_country = bool(country) and prov.country == country
        if not mine and not (in_country and not target_ids):
            continue
        reasons: list[str] = []
        score = 0
        offers = []
        for o in mine:
            name = by_id[o.procedure_id].name
            price = f" (est. {o.estimated_price} {o.currency or ''})".replace(" )", ")") if o.estimated_price is not None else ""
            offers.append(name + price)
        if mine:
            score += 3
            reasons.append("Offers " + ", ".join(offers))
        if in_country:
            score += 2
            reasons.append(f"Located in {prov.country}" + (f" ({prov.city})" if prov.city else ""))
        elif country and mine:
            reasons.append(f"Located in {prov.country}, not the preferred {country}")
        if verified.get(prov.id):
            score += 1
            reasons.append(f"{verified[prov.id]} verified credential{'s' if verified[prov.id] != 1 else ''}")
        fit = "STRONG" if mine and (in_country or not country) else "PARTIAL"
        matches.append(
            {
                "provider_id": str(prov.id), "name": prov.name,
                "location": ", ".join(x for x in [prov.city, prov.country] if x),
                "score": score, "fit": fit, "reasons": reasons, "offerings": offers,
            }
        )
    matches.sort(key=lambda m: (-m["score"], m["name"]))

    none_reason = None
    if not matches:
        if not target_ids and not country:
            none_reason = "This enquiry names no treatment or destination Klaros can match on yet."
        elif target_ids:
            none_reason = "No active provider offers " + ", ".join(p.name for p in target) + " yet — add an offering on a provider."
        else:
            none_reason = f"No active provider is configured in {country}."
    return {
        "basis": {
            "procedures": [p.name for p in target],
            "procedure_source": basis_source,
            "destination_country": country,
            "criteria": ["Treatment offered (active offering)", "Destination country", "Verified credentials"],
        },
        "matches": matches,
        "none_reason": none_reason,
    }


_TOOL_LABEL = {
    "crm.create_lead": "Lead created",
    "crm.update_lead": "Lead updated",
    "crm.qualify_lead": "Qualification recorded",
    "crm.ai_qualify_lead_advisory": "AI qualification advisory generated",
    "crm.create_appointment": "Appointment booked",
    "crm.cancel_appointment": "Appointment cancelled",
    "crm.reschedule_appointment": "Appointment rescheduled",
    "crm.create_note": "Note added",
    "marketing.enroll_lead_in_nurture": "Enrolled in a nurture sequence",
}
_ACTOR = {"AI": "by AI", "USER": "by a team member", "SYSTEM": "automatically"}


async def lead_operations(tenant_id: uuid.UUID, lead_id: uuid.UUID) -> dict[str, Any] | None:
    matched = await match_providers(tenant_id, lead_id)
    if matched is None:
        return None
    async with async_session_maker() as session:
        await set_tenant_context(session, tenant_id)
        lead = (await session.execute(select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id))).scalar_one()
        patient = (await session.execute(select(PatientLead).where(PatientLead.lead_id == lead_id))).scalar_one()
        procedure = (
            (await session.execute(select(Procedure).where(Procedure.id == patient.procedure_id))).scalar_one_or_none()
            if patient.procedure_id
            else None
        )
        consults = (
            await session.execute(
                select(Consultation, Provider.name)
                .join(Appointment, Appointment.id == Consultation.appointment_id)
                .join(Provider, Provider.id == Consultation.provider_id)
                .where(Appointment.lead_id == lead_id, Consultation.tenant_id == tenant_id)
                .order_by(Consultation.created_at)
            )
        ).all()
        audits = (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.tenant_id == tenant_id, AuditLog.entity_id == lead_id)
                .order_by(AuditLog.created_at)
            )
        ).scalars().all()

    timeline: list[dict[str, Any]] = [
        {"at": lead.created_at.isoformat(), "kind": "received", "text": f"Lead received from {_humanize_source(lead.source)}"}
    ]
    for a in audits:
        label = _TOOL_LABEL.get(a.tool or a.action)
        if label is None or (a.tool == "crm.create_lead" and a.result == "success"):
            continue
        suffix = "" if a.result == "success" else " (failed)"
        timeline.append({"at": a.created_at.isoformat(), "kind": "action", "text": f"{label} {_ACTOR.get(a.actor_type, '')}".strip() + suffix})
    for c, pname in consults:
        timeline.append({"at": c.created_at.isoformat(), "kind": "consultation", "text": f"Consultation {c.status.lower()} with {pname}"})
    timeline.sort(key=lambda t: t["at"])

    # The next action: only things a person can actually do in Klaros today.
    top = matched["matches"][0] if matched["matches"] else None
    if lead.status == "NEW":
        nxt = {"text": "Review this enquiry and contact the patient, then mark the lead as contacted.", "route": None, "state": "READY"}
    elif consults:
        nxt = {"text": "Follow the consultation and record any referral.", "route": "/medical-tourism/referrals", "state": "READY"}
    elif top:
        nxt = {"text": f"Schedule a consultation — {top['name']} is the closest match.", "route": "/medical-tourism/consultations", "state": "READY"}
    else:
        nxt = {"text": matched["none_reason"] or "Add a provider that can handle this enquiry.", "route": "/medical-tourism/providers", "state": "CONFIGURATION_REQUIRED"}

    return {
        "patient": {
            "treatment": procedure.name if procedure else None,
            "destination_country": patient.preferred_destination_country,
            "medical_history_summary": patient.medical_history_summary,
            "travel_start": patient.travel_start_date.isoformat() if patient.travel_start_date else None,
            "travel_end": patient.travel_end_date.isoformat() if patient.travel_end_date else None,
            "has_insurance": patient.has_insurance,
            "insurance_notes": patient.insurance_notes,
        },
        "matching": matched,
        "consultations": [
            {"id": str(c.id), "provider": pname, "status": c.status, "notes": c.notes, "created_at": c.created_at.isoformat()}
            for c, pname in consults
        ],
        "timeline": timeline,
        "next_action": nxt,
    }


async def _operations(tenant_id: uuid.UUID) -> dict[str, Any]:
    async with async_session_maker() as session:
        await set_tenant_context(session, tenant_id)

        async def count(model, *where):
            return int((await session.execute(select(func.count()).select_from(model).where(model.tenant_id == tenant_id, *where))).scalar_one())

        providers = await count(Provider, Provider.status == ProviderStatus.ACTIVE)
        procedures = await count(Procedure, Procedure.status == ProcedureStatus.ACTIVE)
        offerings = await count(ProviderProcedure, ProviderProcedure.status == ProviderProcedureStatus.ACTIVE)
        patients = await count(PatientLead)
        commissions = await count(ReferralCommission)
        consult_rows = (
            await session.execute(
                select(Consultation.status, func.count()).where(Consultation.tenant_id == tenant_id).group_by(Consultation.status)
            )
        ).all()
        by_country = Counter(
            c for (c,) in (await session.execute(select(Provider.country).where(Provider.tenant_id == tenant_id, Provider.status == ProviderStatus.ACTIVE))).all()
        )
        patients_with_consult = int(
            (
                await session.execute(
                    select(func.count(func.distinct(Appointment.lead_id)))
                    .select_from(Consultation)
                    .join(Appointment, Appointment.id == Consultation.appointment_id)
                    .where(Consultation.tenant_id == tenant_id, Appointment.lead_id.is_not(None))
                )
            ).scalar_one()
        )
    consultations = sum(n for _, n in consult_rows)
    async with async_session_maker() as session:
        await set_tenant_context(session, tenant_id)
        recent_providers = (await session.execute(select(Provider).where(Provider.tenant_id == tenant_id).order_by(Provider.created_at.desc()).limit(4))).scalars().all()
        recent_consults = (
            await session.execute(
                select(Consultation, Provider.name)
                .join(Provider, Provider.id == Consultation.provider_id)
                .where(Consultation.tenant_id == tenant_id)
                .order_by(Consultation.created_at.desc())
                .limit(4)
            )
        ).all()
    activity = [{"at": p.created_at, "kind": "data", "text": f"Provider added: {p.name}", "route": "/medical-tourism/providers"} for p in recent_providers]
    activity += [{"at": c.created_at, "kind": "consultation", "text": f"Consultation {c.status.lower()} with {n}", "route": "/medical-tourism/consultations"} for c, n in recent_consults]
    return {
        "activity": activity,
        "metrics": [
            {"key": "patient_leads", "label": "Patient enquiries", "value": patients, "route": "/medical-tourism/leads"},
            {"key": "consultations", "label": "Consultations", "value": consultations, "route": "/medical-tourism/consultations"},
            {"key": "patients_with_consultation", "label": "Patients with a consultation", "value": patients_with_consult, "route": "/medical-tourism/consultations"},
            {"key": "referral_commissions", "label": "Referral commissions", "value": commissions, "route": "/medical-tourism/referrals"},
        ],
        "data": [
            {"key": "providers", "label": "Providers", "count": providers, "route": "/medical-tourism/providers"},
            {"key": "procedures", "label": "Procedures", "count": procedures, "route": "/medical-tourism/procedures"},
            {"key": "offerings", "label": "Provider offerings", "count": offerings, "route": "/medical-tourism/providers"},
            {"key": "patient_leads", "label": "Patient enquiries", "count": patients, "route": "/medical-tourism/leads"},
            {"key": "consultations", "label": "Consultations", "count": consultations, "route": "/medical-tourism/consultations"},
            {"key": "referral_commissions", "label": "Referral commissions", "count": commissions, "route": "/medical-tourism/referrals"},
        ],
        "breakdowns": [
            {"key": "providers_by_country", "label": "Providers by country", "items": [{"label": c, "value": n} for c, n in by_country.most_common(8)]},
            {"key": "consultations_by_status", "label": "Consultations by status", "items": [{"label": s.replace("_", " ").title(), "value": n} for s, n in consult_rows]},
        ],
        "lead_stages": [
            {
                "key": "provider_matching", "label": "Provider matching", "kind": "ASSISTED",
                "state": "READY" if providers and offerings else "CONFIGURATION_REQUIRED",
                "detail": "Klaros suggests providers on each patient lead." if providers and offerings else "Add providers and what they offer so Klaros can suggest matches.",
                "route": "/medical-tourism/providers",
            },
            {
                "key": "consultation", "label": "Consultation", "kind": "MANUAL", "state": "READY",
                "detail": "Your team schedules consultations — nothing is booked automatically.", "route": "/medical-tourism/consultations",
            },
            {
                "key": "referral", "label": "Referral & commission", "kind": "MANUAL", "state": "READY",
                "detail": "Your team records referrals and commissions.", "route": "/medical-tourism/referrals",
            },
        ],
    }


register_operations_provider("medical_tourism", _operations)
