"""Tell the patient what is happening with their enquiry, in plain words, at the two moments that matter after the first acknowledgement.

  referral sent    "As you agreed, we shared your enquiry with <clinic>."
  clinic accepted  "<clinic> accepted your enquiry and will be in touch."

Each is triggered by a person's own action (approving and sending the referral, recording the clinic's answer), goes only through the consent-guarded channel (so it is sent only if the
patient's contact consent is on record), is sent once per referral and stage, and says nothing about what the patient asked for or their health. It never raises: a failed update must not
stop the referral work that caused it, and a refusal or failure is written down rather than hidden.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import func, select

from app.db.session import set_tenant_context
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.crm import Lead
from app.models.medical_tourism import Provider
from app.models.medical_tourism_referral import ProviderReferral
from app.models.organization import Organization

logger = structlog.get_logger(__name__)

SENT_ACTION = "mt.patient_update.sent"
SKIPPED_ACTION = "mt.patient_update.skipped"
STAGES = ("referral_sent", "clinic_accepted")


def compose(stage: str, *, first_name: str, clinic: str, business: str) -> tuple[str, str]:
    """(subject, body). Names the clinic the patient approved and nothing about their treatment or health."""
    hello = f"Hello {first_name or 'there'},\n\n"
    close = f"\n\nIf you have any questions, just reply to this email.\n\n{business}"
    if stage == "referral_sent":
        return (f"Your enquiry has been passed to {clinic}",
                hello + f"As you agreed, we have shared your enquiry with {clinic}. They may contact you directly. We will stay in touch and let you know what happens next." + close)
    if stage == "clinic_accepted":
        return (f"{clinic} has accepted your enquiry",
                hello + f"Good news: {clinic} has accepted your enquiry and will be in touch to arrange the next step." + close)
    raise ValueError(f"unknown stage {stage}")


async def notify(session_factory, tenant_id: uuid.UUID, referral_id: uuid.UUID, stage: str, actor_id: uuid.UUID | None = None) -> dict[str, Any]:
    """Send the update for this referral and stage if it has not been sent. Returns {'sent': bool, 'reason': str|None}. Never raises."""
    try:
        return await _notify(session_factory, tenant_id, referral_id, stage, actor_id)
    except Exception as exc:  # noqa: BLE001 - housekeeping must never break referral work
        logger.warning("mt_patient_update_failed", stage=stage, error=type(exc).__name__)
        return {"sent": False, "reason": "error"}


async def _record(session_factory, tenant_id, actor_id, action: str, referral_id: uuid.UUID, stage: str, reason: str | None = None) -> None:
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        session.add(AuditLog(tenant_id=tenant_id, actor_type=ActorType.USER if actor_id else ActorType.SYSTEM, actor_id=actor_id, action=action,
                             entity_type="medical_tourism_provider_referral", entity_id=referral_id, input_summary={"stage": stage, **({"reason": reason} if reason else {})}, result="success"))
        await session.commit()


async def _notify(session_factory, tenant_id, referral_id, stage, actor_id) -> dict[str, Any]:
    from app.communications.base import MessageTemplate
    from app.communications.email_config import email_configured
    from app.communications.factory import get_communication_provider

    if stage not in STAGES:
        return {"sent": False, "reason": "unknown_stage"}
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        referral = (await session.execute(select(ProviderReferral).where(ProviderReferral.id == referral_id, ProviderReferral.tenant_id == tenant_id))).scalar_one_or_none()
        if referral is None:
            return {"sent": False, "reason": "not_found"}
        lead = await session.get(Lead, referral.lead_id)
        provider = await session.get(Provider, referral.provider_id)
        org = await session.get(Organization, tenant_id)
        already = (await session.execute(select(func.count()).select_from(AuditLog).where(
            AuditLog.tenant_id == tenant_id, AuditLog.action == SENT_ACTION, AuditLog.entity_id == referral_id, AuditLog.input_summary["stage"].as_string() == stage))).scalar_one()
    if lead is None or provider is None or already:
        return {"sent": False, "reason": "already_sent" if already else "not_found"}
    to = (lead.email or "").strip()
    if not to:
        return {"sent": False, "reason": "no_email"}
    if not email_configured():
        return {"sent": False, "reason": "email_not_configured"}
    business = org.name if org is not None else "our team"
    first = (lead.name or "").strip().split(" ")[0]
    subject, body = compose(stage, first_name=first, clinic=provider.name, business=business)
    result = await get_communication_provider(session_factory).deliver_email(tenant_id, to=to, subject=subject, body=body, template=MessageTemplate.LEAD_FOLLOW_UP, lead_id=lead.id)
    if result.blocked:
        await _record(session_factory, tenant_id, actor_id, SKIPPED_ACTION, referral_id, stage, "no_contact_consent")
        return {"sent": False, "reason": "no_contact_consent"}
    if not result.sent:
        return {"sent": False, "reason": "not_delivered"}
    await _record(session_factory, tenant_id, actor_id, SENT_ACTION, referral_id, stage)
    return {"sent": True, "reason": None}
