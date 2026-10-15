"""medical_tourism.* referral tools: let a governed agent work the referral pipeline the way a coordinator does, without any new way around the rules.

  * `list_referral_work`  read-only. What needs action right now: approved referrals not yet sent, sent referrals the clinic has not answered, accepted referrals with no
                          reported outcome, and clinics that have verified evidence but are not yet marked verified. Ids and clinic names only, never patient data.
  * `send_referral`       sends an approved referral. It is the same `mt_referrals.send_referral` a coordinator's button uses, so the whole gate still applies (a VERIFIED clinic,
                          the patient's recorded approval, only the approved fields, once only). APPROVAL_REQUIRED by default because it moves patient data to a clinic.
  * `remind_clinic`       asks a clinic that has not answered to confirm, by e-mail to its own stored address. It carries a reference only, never patient data. APPROVAL_REQUIRED by
                          default, and a clinic is reminded at most once every three days per referral.

A tenant that wants its crew to do these on its own sets the tool to AUTO in its own automation policy; nothing here can mark a clinic verified or record a patient's approval.
"""

import uuid
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel
from sqlalchemy import func, select

from app.db.session import async_session_maker, set_tenant_context
from app.models.audit_log import AuditLog
from app.models.medical_tourism import CredentialStatus, Provider, ProviderCredential
from app.models.medical_tourism_referral import ProviderReferral, ReferralStatus
from app.models.rbac import Permission
from app.services import mt_referrals
from app.tools.base import ExecutionContext, Tool

AWAITING_RESPONSE_DAYS = 3
AWAITING_OUTCOME_DAYS = 14
REMINDER_EVERY_DAYS = 3
REMINDER_ACTION = "mt.referral.reminder_sent"
INVITE_ACTION = "mt.provider.invited"
INVITE_EVERY_DAYS = 14


def _aware(value: datetime | None) -> datetime | None:
    return value if value is None or value.tzinfo else value.replace(tzinfo=timezone.utc)


class EmptyInput(BaseModel):
    pass


class ReferralWorkOutput(BaseModel):
    approved_not_sent: list[dict]
    awaiting_clinic_response: list[dict]
    awaiting_outcome: list[dict]
    ready_for_verification: list[dict]
    clinics_to_invite: list[dict] = []
    clinics_without_commission_terms: list[dict] = []  # clinics already sent referrals but with no agreed commission: nothing will be earned from them


class ListReferralWork(Tool):
    name = "medical_tourism.list_referral_work"
    description = (
        "List what needs action in the referral pipeline: approved referrals not yet sent, sent referrals the clinic has not answered for 3+ days, accepted referrals with no "
        "outcome for 14+ days, clinics that have been sent referrals but have no agreed commission terms, clinics with verified evidence that a person has not yet marked verified, and clinics with a contact email that have not yet been invited to send their credentials. "
        "Ids and clinic names only. Read-only."
    )
    input_schema = EmptyInput
    output_schema = ReferralWorkOutput
    required_permission = Permission.READ_MEDICAL_TOURISM

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> ReferralWorkOutput:
        now = datetime.now(timezone.utc)
        tid = context.tenant_id
        async with async_session_maker() as session:
            await set_tenant_context(session, tid)
            refs = list((await session.execute(select(ProviderReferral).where(ProviderReferral.tenant_id == tid, ProviderReferral.status.in_(
                [ReferralStatus.APPROVED, ReferralStatus.SENT, ReferralStatus.ACCEPTED])))).scalars())
            names = {p.id: p.name for p in (await session.execute(select(Provider).where(Provider.tenant_id == tid))).scalars()}
            approved, waiting, outcome = [], [], []
            for r in refs:
                item = {"referral_id": str(r.id), "provider_id": str(r.provider_id), "clinic": names.get(r.provider_id)}
                if r.status == ReferralStatus.APPROVED:
                    approved.append(item)
                elif r.status == ReferralStatus.SENT and (sent := _aware(r.sent_at)) and now - sent >= timedelta(days=AWAITING_RESPONSE_DAYS):
                    waiting.append({**item, "days_since_sent": (now - sent).days})
                elif r.status == ReferralStatus.ACCEPTED and (resp := _aware(r.clinic_response_at)) and now - resp >= timedelta(days=AWAITING_OUTCOME_DAYS):
                    outcome.append({**item, "days_since_accepted": (now - resp).days})
            evidence = (await session.execute(
                select(ProviderCredential.provider_id, func.count()).where(ProviderCredential.tenant_id == tid, ProviderCredential.status == CredentialStatus.VERIFIED)
                .group_by(ProviderCredential.provider_id))).all()
            with_evidence = {pid for pid, n in evidence if n}
            ready = [
                {"provider_id": str(p.id), "clinic": p.name}
                for p in (await session.execute(select(Provider).where(Provider.tenant_id == tid, Provider.verification_status.in_(["UNVERIFIED", "UNDER_REVIEW"])))).scalars()
                if p.id in with_evidence
            ]
            invited = {e for (e,) in (await session.execute(select(AuditLog.entity_id).where(
                AuditLog.tenant_id == tid, AuditLog.action == INVITE_ACTION, AuditLog.created_at >= now - timedelta(days=INVITE_EVERY_DAYS)))).all()}
            to_invite = [
                {"provider_id": str(p.id), "clinic": p.name}
                for p in (await session.execute(select(Provider).where(Provider.tenant_id == tid, Provider.verification_status.in_(["UNVERIFIED", "UNDER_REVIEW"])))).scalars()
                if (p.contact_email or "").strip() and p.id not in with_evidence and p.id not in invited
            ]
            from app.models.mt_revenue import ProviderTerms

            sent_to = {r.provider_id for r in (await session.execute(select(ProviderReferral).where(ProviderReferral.tenant_id == tid, ProviderReferral.sent_at.is_not(None)))).scalars()}
            with_terms = {t.provider_id for t in (await session.execute(select(ProviderTerms).where(ProviderTerms.tenant_id == tid))).scalars() if t.commission_basis}
            no_terms = [{"provider_id": str(pid), "clinic": names.get(pid)} for pid in sorted(sent_to - with_terms, key=str)]
        return ReferralWorkOutput(approved_not_sent=approved, awaiting_clinic_response=waiting, awaiting_outcome=outcome, ready_for_verification=ready, clinics_to_invite=to_invite,
                                  clinics_without_commission_terms=no_terms)


class SendReferralInput(BaseModel):
    referral_id: uuid.UUID


class SendReferralOutput(BaseModel):
    sent: bool
    newly_sent: bool = False
    reason: str | None = None


class SendReferral(Tool):
    name = "medical_tourism.send_referral"
    description = (
        "Send an approved referral to its clinic. Works only for a VERIFIED clinic with the patient's approval on record, and sends only the approved fields. A repeat sends nothing "
        "more. Returns the refusal reason instead of failing."
    )
    input_schema = SendReferralInput
    output_schema = SendReferralOutput
    required_permission = Permission.COORDINATE_MEDICAL_TOURISM

    async def execute(self, input: SendReferralInput, context: ExecutionContext) -> SendReferralOutput:
        try:
            referral, newly = await mt_referrals.send_referral(async_session_maker, context.tenant_id, input.referral_id, context.actor_id)
        except mt_referrals.ReferralError as exc:
            return SendReferralOutput(sent=False, reason=str(exc) or getattr(exc, "code", "refused"))
        return SendReferralOutput(sent=referral.status in (ReferralStatus.SENT, ReferralStatus.ACCEPTED, ReferralStatus.DECLINED, ReferralStatus.OUTCOME_REPORTED), newly_sent=newly)


class RemindClinicInput(BaseModel):
    referral_id: uuid.UUID


class RemindClinicOutput(BaseModel):
    reminded: bool
    reason: str | None = None


class RemindClinic(Tool):
    name = "medical_tourism.remind_clinic"
    description = (
        "E-mail a clinic that has not answered a sent referral, asking it to confirm. Goes only to the clinic's own stored address and carries a reference only, never patient data. "
        "At most once every 3 days per referral."
    )
    input_schema = RemindClinicInput
    output_schema = RemindClinicOutput
    required_permission = Permission.COORDINATE_MEDICAL_TOURISM

    async def execute(self, input: RemindClinicInput, context: ExecutionContext) -> RemindClinicOutput:
        from app.communications.base import MessageTemplate
        from app.communications.factory import get_clinic_channel
        tid = context.tenant_id
        async with async_session_maker() as session:
            await set_tenant_context(session, tid)
            referral = (await session.execute(select(ProviderReferral).where(ProviderReferral.id == input.referral_id, ProviderReferral.tenant_id == tid))).scalar_one_or_none()
            if referral is None:
                return RemindClinicOutput(reminded=False, reason="referral not found")
            if referral.status != ReferralStatus.SENT:
                return RemindClinicOutput(reminded=False, reason="only a sent referral that the clinic has not answered can be chased")
            provider = await session.get(Provider, referral.provider_id)
            last = (await session.execute(select(func.max(AuditLog.created_at)).where(AuditLog.tenant_id == tid, AuditLog.action == REMINDER_ACTION, AuditLog.entity_id == referral.id))).scalar_one()
        if last is not None and datetime.now(timezone.utc) - _aware(last) < timedelta(days=REMINDER_EVERY_DAYS):
            return RemindClinicOutput(reminded=False, reason="this clinic was reminded in the last 3 days")
        to = (provider.contact_email or "").strip() if provider is not None else ""
        if not to:
            return RemindClinicOutput(reminded=False, reason="the clinic has no contact e-mail on record")
        from app.communications.email_config import email_configured

        if not email_configured():
            return RemindClinicOutput(reminded=False, reason="no real e-mail provider is configured on this deployment")
        body = (
            f"We introduced a patient to {provider.name} and have not yet heard back.\n\nReference: {referral.id}\n"
            "Please reply to confirm whether you can see them, or tell us if you cannot."
        )
        ok = await get_clinic_channel(async_session_maker).send_email(tid, to=to, subject="Following up on a patient introduction", body=body, template=MessageTemplate.REFERRAL_INVITATION)
        if not ok:
            return RemindClinicOutput(reminded=False, reason="the e-mail provider did not deliver the message")
        async with async_session_maker() as session:
            await set_tenant_context(session, tid)
            session.add(AuditLog(tenant_id=tid, actor_type=context.actor_type, actor_id=context.actor_id, action=REMINDER_ACTION, entity_type="medical_tourism_provider_referral", entity_id=referral.id,
                                 input_summary={"provider_id": str(provider.id)}, result="success"))
            await session.commit()
        return RemindClinicOutput(reminded=True)


class InviteClinicInput(BaseModel):
    provider_id: uuid.UUID


class InviteClinicOutput(BaseModel):
    invited: bool
    reason: str | None = None


class InviteClinic(Tool):
    name = "medical_tourism.invite_clinic"
    description = (
        "E-mail a clinic that is not yet verified and has a contact address, introducing the business and asking it to reply with its licence or accreditation. Goes only to the clinic's own "
        "stored address, mentions no patient and promises nothing. At most once every 14 days per clinic. It does not verify the clinic: only a person can, with evidence on record."
    )
    input_schema = InviteClinicInput
    output_schema = InviteClinicOutput
    required_permission = Permission.COORDINATE_MEDICAL_TOURISM

    async def execute(self, input: InviteClinicInput, context: ExecutionContext) -> InviteClinicOutput:
        from app.communications.base import MessageTemplate
        from app.communications.email_config import email_configured
        from app.communications.factory import get_clinic_channel
        from app.models.organization import Organization

        tid = context.tenant_id
        async with async_session_maker() as session:
            await set_tenant_context(session, tid)
            provider = (await session.execute(select(Provider).where(Provider.id == input.provider_id, Provider.tenant_id == tid))).scalar_one_or_none()
            org = await session.get(Organization, tid)
            last = (await session.execute(select(func.max(AuditLog.created_at)).where(AuditLog.tenant_id == tid, AuditLog.action == INVITE_ACTION, AuditLog.entity_id == input.provider_id))).scalar_one()
        if provider is None:
            return InviteClinicOutput(invited=False, reason="clinic not found")
        if provider.verification_status == "VERIFIED":
            return InviteClinicOutput(invited=False, reason="this clinic is already verified")
        if last is not None and datetime.now(timezone.utc) - _aware(last) < timedelta(days=INVITE_EVERY_DAYS):
            return InviteClinicOutput(invited=False, reason="this clinic was invited in the last 14 days")
        to = (provider.contact_email or "").strip()
        if not to:
            return InviteClinicOutput(invited=False, reason="the clinic has no contact e-mail on record")
        if not email_configured():
            return InviteClinicOutput(invited=False, reason="no real e-mail provider is configured on this deployment")
        business = org.name if org is not None else "our team"
        body = (
            f"Hello {provider.name},\n\n{business} helps international patients find clinics for treatment abroad. We would like to list {provider.name} as a partner clinic.\n\n"
            "Before we can introduce any patient, we need to confirm your credentials. Please reply to this email with your current licence or accreditation (for example a certificate or a registry "
            "link), and the best contact for patient enquiries.\n\nThis is an introduction only. We have not shared any patient information, and nothing is agreed until we have confirmed your details.\n"
        )
        ok = await get_clinic_channel(async_session_maker).send_email(tid, to=to, subject=f"Partnership enquiry from {business}", body=body, template=MessageTemplate.REFERRAL_INVITATION)
        if not ok:
            return InviteClinicOutput(invited=False, reason="the e-mail provider did not deliver the message")
        async with async_session_maker() as session:
            await set_tenant_context(session, tid)
            session.add(AuditLog(tenant_id=tid, actor_type=context.actor_type, actor_id=context.actor_id, action=INVITE_ACTION, entity_type="medical_tourism_provider", entity_id=provider.id,
                                 input_summary={"provider_id": str(provider.id)}, result="success"))
            await session.commit()
        return InviteClinicOutput(invited=True)
