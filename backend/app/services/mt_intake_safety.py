"""Safety screening for text that KLAROS itself receives or stores about a Medical Tourism lead (public enquiry form, lead creation and
edits, patient-lead intake, consultation and customer notes). Halla-originated text is screened in HallaEventProcessor; this module gives the
Klaros-originated paths the same outcome with the same classifier (`pilot_safety.assess`, deterministic, no model call).

What a flag does
  * `lead.qualification_status` becomes REQUIRES_HUMAN (LeadStatus is never touched). Nothing automated may then qualify the lead
    (see LeadQualificationService.qualify).
  * One `lead.safety_escalated` event is published, keyed on lead + category so a repeated edit cannot fire it again. The payload carries the lead
    id, the CATEGORY and the origin label. NEVER the text, the name or any contact detail.
What it does not do: diagnose, advise, pick a provider or promise anything; it only labels the text and hands the lead to a person.

Scope: Medical Tourism tenants only (a safety profile of that key, or a tenant gated by the medical_tourism vertical). Any other tenant, including
a Dropshipping tenant, is untouched. If the classifier itself fails on a Medical Tourism tenant the lead is flagged (category SAFETY_CHECK_FAILED):
an unscreened text is never treated as safe.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from app.db.session import set_tenant_context
from app.models.crm import Appointment, Lead, QualificationStatus
from app.models.event import EventType
from app.services import pilot_safety

logger = structlog.get_logger()
SAFETY_CHECK_FAILED = "SAFETY_CHECK_FAILED"


async def _medical_profile(session_factory, tenant_id: uuid.UUID):
    try:
        profile = await pilot_safety.profile_for_tenant(session_factory, tenant_id)
    except Exception:  # noqa: BLE001 - cannot establish the profile: fall back to the (fail-closed) gate below
        profile = None
    if profile is not None:
        return profile if profile.key == pilot_safety.MEDICAL_TOURISM else None
    from app.services.consent_gate import tenant_requires_consent

    # A tenant with no Halla profile is Medical Tourism exactly when the medical_tourism vertical is enabled (the only gated vertical).
    return pilot_safety.PROFILES[pilot_safety.MEDICAL_TOURISM] if await tenant_requires_consent(session_factory, tenant_id) else None


def _resolve_bus(bus: Any):
    if bus is not None:
        return bus
    try:
        from app.api.tool_deps import get_wired_event_bus

        return get_wired_event_bus()
    except Exception:  # noqa: BLE001 - no bus available: the persisted flag is still the source of truth
        return None


async def screen_lead(session_factory, tenant_id: uuid.UUID, lead_id: uuid.UUID, *texts: str | None, source: str, bus: Any = None) -> str | None:
    """Screen `texts` written about `lead_id`. Returns the flagged category, or None when nothing needs a person (or the tenant is not Medical
    Tourism). Never raises: a failure here must not break the write that triggered it, and a Medical Tourism lead is flagged rather than passed."""
    present = [t for t in texts if t and str(t).strip()]
    if not present:
        return None
    try:
        profile = await _medical_profile(session_factory, tenant_id)
        if profile is None:
            return None
        try:
            assessment = pilot_safety.assess(profile, *present)
            category = assessment.category if assessment.requires_human else None
        except Exception:  # noqa: BLE001
            category = SAFETY_CHECK_FAILED
        if category is None:
            return None
        async with session_factory() as session:
            await set_tenant_context(session, tenant_id)
            lead = await session.get(Lead, lead_id)
            if lead is None or lead.tenant_id != tenant_id:
                return None
            lead.qualification_status = QualificationStatus.REQUIRES_HUMAN
            await session.commit()
        logger.warning("mt_intake_safety_escalation", tenant_id=str(tenant_id), lead_id=str(lead_id), category=category, origin=source)
        target = _resolve_bus(bus)
        if target is not None:
            await target.publish(
                tenant_id=tenant_id, event_type=EventType.LEAD_SAFETY_ESCALATED, source="crm.safety", entity_type="lead", entity_id=lead_id,
                payload={"lead_id": str(lead_id), "category": category, "origin": source},
                idempotency_key=f"mt-safety:{lead_id}:{category}",
            )
        return category
    except Exception as exc:  # noqa: BLE001
        logger.error("mt_intake_safety_failed", tenant_id=str(tenant_id), lead_id=str(lead_id), error_type=type(exc).__name__)
        return None


async def screen_appointment(session_factory, tenant_id: uuid.UUID, appointment_id: uuid.UUID, *texts: str | None, source: str, bus: Any = None) -> str | None:
    """Notes on a consultation belong to an appointment, and an appointment to a lead. No lead behind it => nothing to hand to a person."""
    if not any(t and str(t).strip() for t in texts):
        return None
    try:
        async with session_factory() as session:
            await set_tenant_context(session, tenant_id)
            appt = await session.get(Appointment, appointment_id)
            lead_id = appt.lead_id if appt is not None and appt.tenant_id == tenant_id else None
    except Exception:  # noqa: BLE001
        return None
    return await screen_lead(session_factory, tenant_id, lead_id, *texts, source=source, bus=bus) if lead_id else None


async def screen_customer(session_factory, tenant_id: uuid.UUID, customer_id: uuid.UUID, *texts: str | None, source: str, bus: Any = None) -> list[str]:
    """A note on a customer is screened against every lead behind that customer."""
    if not any(t and str(t).strip() for t in texts):
        return []
    try:
        from sqlalchemy import select

        async with session_factory() as session:
            await set_tenant_context(session, tenant_id)
            ids = list((await session.execute(select(Lead.id).where(Lead.tenant_id == tenant_id, Lead.customer_id == customer_id))).scalars())
    except Exception:  # noqa: BLE001
        return []
    out = []
    for lid in ids:
        c = await screen_lead(session_factory, tenant_id, lid, *texts, source=source, bus=bus)
        if c:
            out.append(c)
    return out
