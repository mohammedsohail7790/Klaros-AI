"""Medical Tourism provider referrals: the patient-approved, provider-specific transfer of a lead's information to ONE verified clinic.

THE RULE (enforced here, in the service layer, so routes, retries, queued work and any future caller all pass through it; the UI is never the control):

  Identifiable information about a patient may be transferred to a provider only while ALL of these hold at the moment of the transfer:
    1. the provider is ACTIVE and its verification is currently VERIFIED (a verified status whose every verified credential has expired counts as EXPIRED);
    2. the lead's newest consent evidence grants `store_personal_data` AND `share_with_selected_provider`;
    3. the newest approval the patient gave for THIS provider is GRANTED, was recorded by a person after telling the patient which clinic receives which
       information, and has not been declined or withdrawn since;
    4. every field being sent is on that approval's permitted list, and a health-related field (the treatment) additionally has `store_medical_information`.
  Anything else is BLOCKED with a closed reason code. The check is repeated on every send attempt, so a withdrawal that arrives after approval (from the
  coordinator, or from Halla as newer consent evidence) blocks a send that is still queued or being retried.

Sending is idempotent: one delivery per (referral, approval). A duplicate or retried call never transfers the data twice. The only transport is a
SANDBOX that records what would have been sent: no clinic is contacted, and a non-sandbox transport fails closed until one is deliberately configured.

Halla never calls into this module: Halla creates the lead (subject to its own consent evidence) and nothing else.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.session import async_session_maker, set_tenant_context
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.crm import Lead
from app.models.medical_tourism import CredentialStatus, PatientLead, Procedure, Provider, ProviderCredential, ProviderStatus
from app.models.medical_tourism_referral import (
    MEDICAL_FIELDS, SHARE_SCOPE, SHAREABLE_FIELDS, ApprovalDecision, HandoffTask, ProviderReferral, ReferralApproval, ReferralDelivery, ReferralOutcome,
    ReferralStatus, TaskStatus,
)
from app.services import halla_consent
from app.services.consent_gate import STORE_MEDICAL, STORE_PERSONAL

logger = structlog.get_logger(__name__)

_WORDING_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
CHANNELS = ("phone", "email", "in_person", "other")

UNVERIFIED, UNDER_REVIEW, VERIFIED, SUSPENDED, EXPIRED = "UNVERIFIED", "UNDER_REVIEW", "VERIFIED", "SUSPENDED", "EXPIRED"
VERIFICATION_STATUSES = (UNVERIFIED, UNDER_REVIEW, VERIFIED, SUSPENDED, EXPIRED)

_SENT_OR_LATER = (ReferralStatus.SENT, ReferralStatus.ACCEPTED, ReferralStatus.DECLINED, ReferralStatus.OUTCOME_REPORTED)


class ReferralError(Exception):
    """A closed `code` (safe to show and to log) and a short human `message`. Never carries patient data."""

    def __init__(self, code: str, message: str | None = None, *, status: int = 409) -> None:
        self.code, self.status = code, status
        super().__init__(message or code)


@dataclass(frozen=True)
class Gate:
    allowed: bool
    code: str | None
    fields: tuple[str, ...] = ()
    approval_id: uuid.UUID | None = None


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)  # SQLite returns naive datetimes


def _audit(session, tenant_id, actor_id, action: str, entity_type: str, entity_id, summary: dict[str, Any], result: str = "success") -> None:
    session.add(AuditLog(tenant_id=tenant_id, actor_type=ActorType.USER if actor_id else ActorType.SYSTEM, actor_id=actor_id, action=action,
                         entity_type=entity_type, entity_id=entity_id, input_summary=summary, result=result))


# --------------------------------------------------------------------------- provider verification


async def effective_verification(session, tenant_id: uuid.UUID, provider: Provider, today: date | None = None) -> str:
    """The provider's verification NOW. A stored VERIFIED needs at least one VERIFIED credential that has not expired; otherwise it is EXPIRED."""
    status = provider.verification_status or UNVERIFIED
    if status != VERIFIED:
        return status
    today = today or datetime.now(timezone.utc).date()
    creds = (await session.execute(select(ProviderCredential).where(
        ProviderCredential.tenant_id == tenant_id, ProviderCredential.provider_id == provider.id, ProviderCredential.status == CredentialStatus.VERIFIED,
    ))).scalars().all()
    return VERIFIED if any(c.expiry_date is None or c.expiry_date >= today for c in creds) else EXPIRED


async def provider_is_referable(session, tenant_id: uuid.UUID, provider: Provider) -> bool:
    return provider.status == ProviderStatus.ACTIVE and await effective_verification(session, tenant_id, provider) == VERIFIED


async def set_verification(session_factory, tenant_id: uuid.UUID, provider_id: uuid.UUID, status: str, actor_id: uuid.UUID, note: str | None = None) -> Provider:
    if status not in VERIFICATION_STATUSES:
        raise ReferralError("unknown_verification_status", status=422)
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        provider = (await session.execute(select(Provider).where(Provider.id == provider_id, Provider.tenant_id == tenant_id))).scalar_one_or_none()
        if provider is None:
            raise ReferralError("provider_not_found", status=404)
        if status == VERIFIED:
            provider.verification_status = UNDER_REVIEW  # evaluate against evidence, not against the request
            session.add(provider)
            await session.flush()
            has_evidence = await effective_verification(session, tenant_id, _as(provider, VERIFIED)) == VERIFIED
            if not has_evidence:
                raise ReferralError("no_current_verified_credential", "A provider is verified only with at least one verified, unexpired credential on record.", status=409)
        provider.verification_status = status
        provider.verification_updated_by = actor_id
        provider.verification_updated_at = datetime.now(timezone.utc)
        _audit(session, tenant_id, actor_id, "mt.provider.verification_set", "medical_tourism_provider", provider.id, {"status": status, "note": (note or "")[:200]})
        await session.commit()
        await session.refresh(provider)
        return provider


def _as(provider: Provider, status: str) -> Provider:
    provider.verification_status = status
    return provider


# --------------------------------------------------------------------------- approvals and the gate


async def current_approval(session, tenant_id: uuid.UUID, lead_id: uuid.UUID, provider_id: uuid.UUID) -> ReferralApproval | None:
    rows = (await session.execute(select(ReferralApproval).where(
        ReferralApproval.tenant_id == tenant_id, ReferralApproval.lead_id == lead_id, ReferralApproval.provider_id == provider_id,
    ))).scalars().all()
    if not rows:
        return None
    return max(rows, key=lambda r: (_utc(r.recorded_at), _utc(r.created_at)))


async def any_provider_approval_granted(session, tenant_id: uuid.UUID, lead_id: uuid.UUID) -> bool:
    rows = (await session.execute(select(ReferralApproval).where(ReferralApproval.tenant_id == tenant_id, ReferralApproval.lead_id == lead_id))).scalars().all()
    newest: dict[uuid.UUID, ReferralApproval] = {}
    for r in rows:
        cur = newest.get(r.provider_id)
        if cur is None or (_utc(r.recorded_at), _utc(r.created_at)) > (_utc(cur.recorded_at), _utc(cur.created_at)):
            newest[r.provider_id] = r
    return any(r.decision == ApprovalDecision.GRANTED for r in newest.values())


async def evaluate_gate(session, tenant_id: uuid.UUID, referral: ProviderReferral, fields: list[str] | None = None) -> Gate:
    """The single decision point. Returns (allowed, closed reason code, the fields that may go). Reads CURRENT state; call it again right before every transfer."""
    if referral.status == ReferralStatus.CANCELLED:
        return Gate(False, "referral_cancelled")
    provider = await session.get(Provider, referral.provider_id)
    if provider is None or provider.tenant_id != tenant_id:
        return Gate(False, "provider_not_found")
    if provider.status != ProviderStatus.ACTIVE:
        return Gate(False, "provider_inactive")
    if await effective_verification(session, tenant_id, provider) != VERIFIED:
        return Gate(False, "provider_not_verified")
    lead = await session.get(Lead, referral.lead_id)
    if lead is None or lead.tenant_id != tenant_id:
        return Gate(False, "lead_not_found")
    if lead.name == halla_consent.ERASED_NAME:
        return Gate(False, "lead_erased")
    state = await halla_consent.state_for(session, tenant_id, halla_lead_id=lead.external_id, lead_id=lead.id)
    if not state.allows(STORE_PERSONAL):
        return Gate(False, "no_personal_data_consent")
    if not state.allows(SHARE_SCOPE):
        return Gate(False, "no_share_consent")
    approval = await current_approval(session, tenant_id, lead.id, provider.id)
    if approval is None:
        return Gate(False, "no_provider_approval")
    if approval.decision == ApprovalDecision.WITHDRAWN:
        return Gate(False, "approval_withdrawn")
    if approval.decision == ApprovalDecision.DECLINED:
        return Gate(False, "approval_declined")
    if not approval.patient_informed:
        return Gate(False, "approval_not_informed")
    permitted = [f for f in SHAREABLE_FIELDS if f in set(approval.permitted_fields or [])]
    wanted = permitted if fields is None else list(fields)
    if not wanted or any(f not in permitted for f in wanted):
        return Gate(False, "fields_not_permitted")
    if any(f in MEDICAL_FIELDS for f in wanted) and not state.allows(STORE_MEDICAL):
        return Gate(False, "no_medical_consent")
    return Gate(True, None, tuple(wanted), approval.id)


# --------------------------------------------------------------------------- referral lifecycle


async def create_referral(session_factory, tenant_id: uuid.UUID, lead_id: uuid.UUID, provider_id: uuid.UUID, actor_id: uuid.UUID, procedure_id: uuid.UUID | None = None) -> tuple[ProviderReferral, bool]:
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        lead = (await session.execute(select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id))).scalar_one_or_none()
        provider = (await session.execute(select(Provider).where(Provider.id == provider_id, Provider.tenant_id == tenant_id))).scalar_one_or_none()
        if lead is None or provider is None:
            raise ReferralError("not_found", status=404)
        if not await provider_is_referable(session, tenant_id, provider):
            raise ReferralError("provider_not_verified", "Only an active, verified provider can be referred to.")
        existing = (await session.execute(select(ProviderReferral).where(
            ProviderReferral.tenant_id == tenant_id, ProviderReferral.lead_id == lead_id, ProviderReferral.provider_id == provider_id))).scalar_one_or_none()
        if existing is not None:
            return existing, False
        if procedure_id is not None:
            proc = await session.get(Procedure, procedure_id)
            if proc is None or proc.tenant_id != tenant_id:
                raise ReferralError("not_found", status=404)
        referral = ProviderReferral(tenant_id=tenant_id, lead_id=lead_id, provider_id=provider_id, procedure_id=procedure_id, status=ReferralStatus.DRAFT, created_by=actor_id, permitted_fields=[])
        session.add(referral)
        await session.flush()  # the id is assigned here, so the audit row can name this referral
        _audit(session, tenant_id, actor_id, "mt.referral.created", "medical_tourism_provider_referral", referral.id, {"provider_id": str(provider_id)})
        try:
            await session.commit()
        except IntegrityError:  # a concurrent identical request created it first: return that one
            await session.rollback()
            await set_tenant_context(session, tenant_id)
            existing = (await session.execute(select(ProviderReferral).where(
                ProviderReferral.tenant_id == tenant_id, ProviderReferral.lead_id == lead_id, ProviderReferral.provider_id == provider_id))).scalar_one()
            return existing, False
        await session.refresh(referral)
        return referral, True


async def record_approval(session_factory, tenant_id: uuid.UUID, referral_id: uuid.UUID, actor_id: uuid.UUID, *, decision: str, permitted_fields: list[str],
                          patient_informed: bool, wording_version: str, channel: str | None = None) -> tuple[ProviderReferral, ReferralApproval]:
    """A coordinator records what the PATIENT decided about sharing with THIS provider. Append-only; the newest row is the decision."""
    if decision not in (ApprovalDecision.GRANTED, ApprovalDecision.DECLINED, ApprovalDecision.WITHDRAWN):
        raise ReferralError("unknown_decision", status=422)
    if not isinstance(wording_version, str) or not _WORDING_RE.match(wording_version):
        raise ReferralError("wording_version_required", "Say which wording the patient was shown (a short label).", status=422)
    if channel is not None and channel not in CHANNELS:
        raise ReferralError("unknown_channel", status=422)
    fields = [f for f in SHAREABLE_FIELDS if f in set(permitted_fields or [])]
    if decision == ApprovalDecision.GRANTED:
        if patient_informed is not True:
            raise ReferralError("patient_must_be_informed", "Record a grant only after telling the patient which clinic will receive which information.", status=422)
        if not fields or set(permitted_fields) - set(SHAREABLE_FIELDS):
            raise ReferralError("fields_required", "List the information the patient agreed to share (only listed fields can ever be sent).", status=422)
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        referral = (await session.execute(select(ProviderReferral).where(ProviderReferral.id == referral_id, ProviderReferral.tenant_id == tenant_id))).scalar_one_or_none()
        if referral is None:
            raise ReferralError("not_found", status=404)
        lead = await session.get(Lead, referral.lead_id)
        provider = await session.get(Provider, referral.provider_id)
        if decision == ApprovalDecision.GRANTED:
            if referral.status in (ReferralStatus.CANCELLED, ReferralStatus.OUTCOME_REPORTED):
                raise ReferralError("referral_closed")
            if not await provider_is_referable(session, tenant_id, provider):
                raise ReferralError("provider_not_verified", "Only an active, verified provider can receive a referral.")
            state = await halla_consent.state_for(session, tenant_id, halla_lead_id=lead.external_id, lead_id=lead.id)
            if not state.allows(STORE_PERSONAL):
                raise ReferralError("no_personal_data_consent", "The patient has not agreed to us keeping their details, so nothing can be shared.")
            if any(f in MEDICAL_FIELDS for f in fields) and not state.allows(STORE_MEDICAL):
                raise ReferralError("no_medical_consent", "The treatment cannot be shared: the patient has not agreed to us keeping it.")
        now = datetime.now(timezone.utc)
        approval = ReferralApproval(tenant_id=tenant_id, lead_id=lead.id, provider_id=provider.id, decision=decision, permitted_fields=fields if decision == ApprovalDecision.GRANTED else [],
                                    patient_informed=bool(patient_informed) if decision == ApprovalDecision.GRANTED else False, wording_version=wording_version,
                                    source="coordinator_recorded", channel=channel, recorded_by=actor_id, recorded_at=now)
        session.add(approval)
        await session.flush()
        # Keep the lead-level evidence in step: SHARE is granted while at least one provider's NEWEST approval is GRANTED.
        want = await any_provider_approval_granted(session, tenant_id, lead.id)
        state = await halla_consent.state_for(session, tenant_id, halla_lead_id=lead.external_id, lead_id=lead.id)
        if want != state.allows(SHARE_SCOPE):
            await halla_consent.record_share_state(session, tenant_id, lead.id, granted=want, wording_version=wording_version, actor_user_id=actor_id)
        if decision == ApprovalDecision.GRANTED:
            if referral.status not in _SENT_OR_LATER:
                referral.status = ReferralStatus.APPROVED
            referral.permitted_fields, referral.approval_id, referral.last_block_reason = fields, approval.id, None
        else:
            was_sent = referral.status in _SENT_OR_LATER
            referral.status = ReferralStatus.CANCELLED if referral.status != ReferralStatus.OUTCOME_REPORTED else referral.status
            referral.last_block_reason = "approval_declined" if decision == ApprovalDecision.DECLINED else "approval_withdrawn"
            if was_sent:  # the data already left: a person must now ask the clinic to remove it
                from app.services import mt_handoff
                await mt_handoff.ensure_task(session, tenant_id, lead.id, kind="referral_followup", reason_code="consent_withdrawn_after_send", dedupe_key=f"withdrawn:{approval.id}")
        _audit(session, tenant_id, actor_id, f"mt.referral.approval_{decision.lower()}", "medical_tourism_provider_referral", referral.id,
               {"provider_id": str(provider.id), "fields": fields, "wording_version": wording_version, "channel": channel, "patient_informed": bool(patient_informed)})
        await session.commit()
        await session.refresh(referral)
        await session.refresh(approval)
        return referral, approval


# --------------------------------------------------------------------------- the transport (sandbox only)


class SandboxTransport:
    """Records what WOULD be sent. No network, no clinic contacted."""

    name = "sandbox"

    async def send(self, provider: Provider, payload: dict[str, Any]) -> None:
        return None


class EmailTransport:
    """Emails the referral to the clinic's OWN stored contact address, and nowhere else (the recipient is never taken from the caller). It carries only the fields the patient
    approved plus a reference. Delivery goes through the platform's real e-mail provider; if none is configured on this deployment the send FAILS and nothing is marked sent."""

    name = "email"

    def __init__(self, session_factory, tenant_id: uuid.UUID) -> None:
        self._session_factory, self._tenant_id = session_factory, tenant_id

    async def send(self, provider: Provider, payload: dict[str, Any]) -> None:
        from app.communications.base import MessageTemplate
        from app.communications.factory import get_clinic_channel

        to = (provider.contact_email or "").strip()
        if not to:
            raise RuntimeError("the clinic has no contact e-mail on record")
        from app.core.config import get_settings

        settings = get_settings()
        if not (settings.SENDGRID_API_KEY and settings.SENDGRID_FROM_EMAIL):
            raise RuntimeError("no real e-mail provider is configured on this deployment")
        channel = get_clinic_channel(self._session_factory)
        lines = [f"{k.replace('_', ' ').capitalize()}: {v}" for k, v in payload.items() if k != "reference"]
        body = (
            f"A patient has asked to be introduced to {provider.name}.\n\n" + "\n".join(lines) +
            f"\n\nReference: {payload.get('reference')}\nPlease reply to confirm whether you can see this patient. Share only what is needed to arrange the consultation."
        )
        ok = await channel.send_email(self._tenant_id, to=to, subject="New patient introduction", body=body, template=MessageTemplate.REFERRAL_INVITATION)
        if not ok:
            raise RuntimeError("the e-mail provider did not deliver the message")


def get_transport(name: str | None = None):
    from app.core.config import get_settings

    chosen = name or getattr(get_settings(), "MT_REFERRAL_TRANSPORT", "sandbox")
    if chosen == "sandbox":
        return SandboxTransport()
    raise ReferralError("transport_not_configured", "No clinic transport is configured; referrals can only be recorded in the sandbox.", status=503)


def _resolve_transport(session_factory, tenant_id: uuid.UUID):
    """The configured transport. `email` needs the tenant (to log the send), everything else is `get_transport`."""
    from app.core.config import get_settings

    if getattr(get_settings(), "MT_REFERRAL_TRANSPORT", "sandbox") == "email":
        return EmailTransport(session_factory, tenant_id)
    return get_transport()


async def _payload(session, tenant_id: uuid.UUID, referral: ProviderReferral, lead: Lead, fields: tuple[str, ...]) -> dict[str, Any]:
    patient = (await session.execute(select(PatientLead).where(PatientLead.tenant_id == tenant_id, PatientLead.lead_id == lead.id))).scalar_one_or_none()
    proc = await session.get(Procedure, referral.procedure_id or (patient.procedure_id if patient else None)) if (referral.procedure_id or (patient and patient.procedure_id)) else None
    source = {
        "name": lead.name, "phone": lead.phone, "email": lead.email,
        "preferred_language": getattr(patient, "preferred_language", None), "preferred_contact_time": getattr(patient, "preferred_contact_time", None),
        "destination": (patient.preferred_destination_country or getattr(patient, "destination_text", None)) if patient else None,
        "timeframe": getattr(patient, "timeframe", None),
        "procedure": (proc.name if proc is not None else getattr(patient, "procedure_text", None)) if patient or proc else None,
    }
    body = {f: source[f] for f in fields if source.get(f)}
    return {"reference": str(referral.id), **body}


async def send_referral(session_factory, tenant_id: uuid.UUID, referral_id: uuid.UUID, actor_id: uuid.UUID | None, transport=None) -> tuple[ProviderReferral, bool]:
    """Transfer the permitted data to the provider. Returns (referral, newly_sent). Safe to call again: a repeat after success returns the existing delivery."""
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        referral = (await session.execute(select(ProviderReferral).where(ProviderReferral.id == referral_id, ProviderReferral.tenant_id == tenant_id))).scalar_one_or_none()
        if referral is None:
            raise ReferralError("not_found", status=404)
        if referral.status in _SENT_OR_LATER:
            return referral, False  # already transferred: a duplicate or retried call is a no-op
        gate = await evaluate_gate(session, tenant_id, referral)
        if not gate.allowed:
            referral.last_block_reason = gate.code
            _audit(session, tenant_id, actor_id, "mt.referral.send_blocked", "medical_tourism_provider_referral", referral.id, {"code": gate.code}, result="failure")
            await session.commit()
            raise ReferralError(gate.code or "blocked", "Referral data was not sent: " + (gate.code or "blocked").replace("_", " ") + ".")
        key = f"referral:{referral.id}:approval:{gate.approval_id}"
        dup = (await session.execute(select(ReferralDelivery).where(ReferralDelivery.tenant_id == tenant_id, ReferralDelivery.idempotency_key == key))).scalar_one_or_none()
        provider = await session.get(Provider, referral.provider_id)
        lead = await session.get(Lead, referral.lead_id)
        if dup is None:
            payload = await _payload(session, tenant_id, referral, lead, gate.fields)
            chosen = transport or _resolve_transport(session_factory, tenant_id)
            try:
                await chosen.send(provider, payload)
            except Exception:  # noqa: BLE001 - any transport failure is retryable and leaves nothing marked as sent
                referral.send_attempts += 1
                referral.last_block_reason = "transport_failed"
                _audit(session, tenant_id, actor_id, "mt.referral.send_failed", "medical_tourism_provider_referral", referral.id, {"transport": getattr(chosen, "name", "?")}, result="failure")
                await session.commit()
                raise ReferralError("transport_failed", "The referral could not be delivered; it can be retried safely.", status=502) from None
            session.add(ReferralDelivery(tenant_id=tenant_id, referral_id=referral.id, lead_id=lead.id, provider_id=provider.id, idempotency_key=key, transport=getattr(chosen, "name", "?"),
                                         fields_sent=list(gate.fields), payload=payload, sent_at=datetime.now(timezone.utc)))
        referral.status, referral.sent_at, referral.last_block_reason = ReferralStatus.SENT, referral.sent_at or datetime.now(timezone.utc), None
        referral.send_attempts += 1
        _audit(session, tenant_id, actor_id, "mt.referral.sent", "medical_tourism_provider_referral", referral.id, {"fields": list(gate.fields), "provider_id": str(provider.id)})
        try:
            await session.commit()
        except IntegrityError:  # a concurrent identical send won the unique key: that delivery is the one
            await session.rollback()
            await set_tenant_context(session, tenant_id)
            referral = (await session.execute(select(ProviderReferral).where(ProviderReferral.id == referral_id, ProviderReferral.tenant_id == tenant_id))).scalar_one()
            return referral, False
        await session.refresh(referral)
        return referral, dup is None


async def record_clinic_response(session_factory, tenant_id: uuid.UUID, referral_id: uuid.UUID, actor_id: uuid.UUID, *, accepted: bool) -> ProviderReferral:
    """A staff member records what the clinic told them. Only after the referral was actually sent."""
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        referral = (await session.execute(select(ProviderReferral).where(ProviderReferral.id == referral_id, ProviderReferral.tenant_id == tenant_id))).scalar_one_or_none()
        if referral is None:
            raise ReferralError("not_found", status=404)
        if referral.status not in (ReferralStatus.SENT, ReferralStatus.ACCEPTED, ReferralStatus.DECLINED):
            raise ReferralError("not_sent", "A clinic response can be recorded only after the referral was sent.")
        referral.status = ReferralStatus.ACCEPTED if accepted else ReferralStatus.DECLINED
        referral.clinic_response_at, referral.clinic_response_by = datetime.now(timezone.utc), actor_id
        _audit(session, tenant_id, actor_id, "mt.referral.clinic_response", "medical_tourism_provider_referral", referral.id, {"accepted": accepted})
        await session.commit()
        await session.refresh(referral)
        return referral


async def record_outcome(session_factory, tenant_id: uuid.UUID, referral_id: uuid.UUID, actor_id: uuid.UUID, *, outcome: str, evidence: str) -> ProviderReferral:
    """The clinic's REPORTED outcome, with a reference to where it was reported. Never inferred; required before any commission can be confirmed."""
    if outcome not in {o.value for o in ReferralOutcome}:
        raise ReferralError("unknown_outcome", status=422)
    if not evidence or not evidence.strip():
        raise ReferralError("evidence_required", "Say where the clinic reported this (a reference, not clinical detail).", status=422)
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        referral = (await session.execute(select(ProviderReferral).where(ProviderReferral.id == referral_id, ProviderReferral.tenant_id == tenant_id))).scalar_one_or_none()
        if referral is None:
            raise ReferralError("not_found", status=404)
        if referral.status not in (ReferralStatus.ACCEPTED, ReferralStatus.OUTCOME_REPORTED):
            raise ReferralError("clinic_has_not_accepted", "An outcome can be recorded only for a referral the clinic accepted.")
        referral.status, referral.outcome, referral.outcome_evidence = ReferralStatus.OUTCOME_REPORTED, outcome, evidence.strip()[:255]
        referral.outcome_reported_at = datetime.now(timezone.utc)
        _audit(session, tenant_id, actor_id, "mt.referral.outcome_reported", "medical_tourism_provider_referral", referral.id, {"outcome": outcome})
        await session.commit()
        await session.refresh(referral)
        return referral


def referral_to_dict(r: ProviderReferral) -> dict[str, Any]:
    return {
        "id": str(r.id), "lead_id": str(r.lead_id), "provider_id": str(r.provider_id), "status": r.status, "permitted_fields": list(r.permitted_fields or []),
        "sent_at": r.sent_at.isoformat() if r.sent_at else None, "send_attempts": r.send_attempts, "blocked_reason": r.last_block_reason,
        "clinic_response_at": r.clinic_response_at.isoformat() if r.clinic_response_at else None,
        "outcome": r.outcome, "outcome_reported_at": r.outcome_reported_at.isoformat() if r.outcome_reported_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
