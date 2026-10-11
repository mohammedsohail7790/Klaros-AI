"""Consent enforcement for tenants whose business requires explicit consent before personal or health data is kept.

WHO is gated: a tenant is "consent-gated" when its Halla connection carries a safety profile with `requires_consent_evidence`, OR when it has a
vertical enabled whose key is listed in GATED_VERTICAL_KEYS (so a tenant cannot escape the gate by simply not having a Halla connection).
Everyone else is untouched: every function here is a no-op for them.

WHAT is enforced (service layer, so routes, agent tools, imports, public forms and Halla events all pass through it):
  * `store_personal_data`        required to create a lead (name / phone / email)
  * `store_medical_information`  required to keep treatment or free-text health detail (`service_requested`, `description`, health intake fields)
  * `contact`                    required before outbound contact (Halla call / sync)
Claims are made by a PERSON (operator attestation, with their user id) or by the lead themself (public form checkboxes) or come from Halla's own
signed evidence. An AI agent / workflow / system actor can never attest consent.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Iterable

import structlog

from app.models.actor import ActorType

logger = structlog.get_logger()

STORE_PERSONAL, STORE_MEDICAL, CONTACT = "store_personal_data", "store_medical_information", "contact"
ALL_SCOPES = (CONTACT, STORE_PERSONAL, STORE_MEDICAL)
GATED_VERTICAL_KEYS = ("medical_tourism",)

SOURCE_HALLA, SOURCE_WEB, SOURCE_OPERATOR = "halla", "web_form", "operator_attested"


class ConsentRequiredError(Exception):
    """Raised before anything is persisted. `missing` names scopes only; `message` never includes the data that was refused."""

    def __init__(self, missing: Iterable[str], reason: str = "consent_required") -> None:
        self.missing = sorted(set(missing))
        self.reason = reason
        super().__init__(f"{reason}: {', '.join(self.missing)}" if self.missing else reason)


@dataclass(frozen=True)
class ConsentClaim:
    """What the caller asserts the person agreed to. `recorded=True` means the evidence is already in the history (Halla path)."""

    scopes: frozenset[str]
    source: str
    wording_version: str | None = None
    actor_user_id: uuid.UUID | None = None
    recorded: bool = False


def claim_from_tool(raw: dict | None, actor_type: ActorType, actor_id: uuid.UUID | None) -> ConsentClaim | None:
    """A claim supplied through the tool/API layer. Only a signed-in PERSON may attest; anything else yields no claim (=> refused)."""
    if not raw or actor_type != ActorType.USER or actor_id is None:
        return None
    scopes = frozenset(s for s in (raw.get("scopes") or []) if s in ALL_SCOPES)
    return ConsentClaim(scopes, SOURCE_OPERATOR, (raw.get("wording_version") or None), actor_id) if scopes else None


async def tenant_requires_consent(session_factory, tenant_id: uuid.UUID | None) -> bool:
    """True when the tenant is consent-gated. FAILS CLOSED: if the tenant's status cannot be established (any lookup error) it is treated as gated,
    so a database or catalogue hiccup can never turn a Medical Tourism tenant into an ungated one. Only an ESTABLISHED answer leaves a tenant
    ungated: no Halla safety profile requiring evidence, and every gated vertical either absent from the catalogue (an established "not enabled")
    or looked up successfully and not enabled for this tenant. A tenant with no id (system context) has nothing to gate."""
    if tenant_id is None:
        return False
    from app.db.session import set_tenant_context
    from app.services import pilot_safety
    from app.services.vertical_extension_service import VerticalExtensionNotFoundError, VerticalExtensionService

    try:
        async with session_factory() as session:
            await set_tenant_context(session, tenant_id)
            profile = await pilot_safety.profile_in_session(session, tenant_id)
        if profile is not None and profile.requires_consent_evidence:
            return True
        service = VerticalExtensionService(session_factory)
        for key in GATED_VERTICAL_KEYS:
            try:
                if await service.is_enabled_for_organization(tenant_id, key):
                    return True
            except VerticalExtensionNotFoundError:
                continue  # the catalogue has no such vertical: an established "not enabled", not a lookup failure
        return False
    except Exception:  # noqa: BLE001 - status unknown: fail closed, never open
        logger.warning("consent_gate_status_unknown_failing_closed", tenant_id=str(tenant_id))
        return True


async def approved_wording_versions(session_factory, tenant_id: uuid.UUID) -> list[str]:
    """Wording labels the OWNER approved for public forms (connection metadata `approved_wording_versions`). Empty => public intake stays closed."""
    from app.db.session import set_tenant_context
    from app.models.integration import IntegrationConnection
    from sqlalchemy import select

    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        conn = (await session.execute(select(IntegrationConnection).where(IntegrationConnection.tenant_id == tenant_id, IntegrationConnection.provider == "halla"))).scalar_one_or_none()
    meta = conn.connection_metadata if conn is not None and isinstance(conn.connection_metadata, dict) else {}
    versions = meta.get("approved_wording_versions")
    return [v for v in versions if isinstance(v, str) and v] if isinstance(versions, list) else []


def check_create(claim: ConsentClaim | None, *, has_medical_fields: bool) -> None:
    """Raise ConsentRequiredError unless the claim permits storing this new record."""
    need = [STORE_PERSONAL] + ([STORE_MEDICAL] if has_medical_fields else [])
    have = claim.scopes if claim is not None else frozenset()
    missing = [s for s in need if s not in have]
    if missing:
        raise ConsentRequiredError(missing)


async def ensure_not_gated(session_factory, tenant_id: uuid.UUID | None, reason: str) -> None:
    """For features that cannot carry per-person consent evidence (customer imports, list building, the voice receptionist ...): a consent-gated
    tenant is refused with `reason`; everyone else is untouched."""
    if await tenant_requires_consent(session_factory, tenant_id):
        raise ConsentRequiredError([], reason)


async def customer_leads_allow(session_factory, tenant_id: uuid.UUID, customer_id: uuid.UUID, scope: str) -> bool:
    """True only when the customer is linked to at least one lead and EVERY linked lead's newest evidence grants `scope`."""
    from sqlalchemy import select

    from app.db.session import set_tenant_context
    from app.models.crm import Lead
    from app.services import halla_consent

    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        leads = (await session.execute(select(Lead).where(Lead.tenant_id == tenant_id, Lead.customer_id == customer_id))).scalars().all()
        if not leads:
            return False
        for lead in leads:
            state = await halla_consent.state_for(session, tenant_id, halla_lead_id=lead.external_id, lead_id=lead.id)
            if not state.allows(scope):
                return False
    return True


async def contact_decision(
    session_factory, tenant_id: uuid.UUID, *, email: str | None = None, phone: str | None = None,
    lead_id: uuid.UUID | None = None, customer_id: uuid.UUID | None = None,
) -> tuple[bool, str]:
    """(allowed, reason_code) for an outbound message to `email` / `phone`. Fails closed.

    UNBOUND (no lead_id / customer_id): the address must resolve to exactly ONE patient (one customer, or one customer-less lead) and every lead of
    that patient must have newest evidence granting `contact`. An address shared by several patients (family e-mail, shared phone) is AMBIGUOUS and
    is blocked: one person's consent must not authorise a message about another.
    BOUND: the validated lead / customer must belong to the tenant, the address must be that patient's own, and only that patient's leads are
    checked, so a shared address can still be used for the patient the caller can prove the message is about."""
    from sqlalchemy import or_, select

    from app.db.session import set_tenant_context
    from app.models.crm import Customer, Lead
    from app.services import halla_consent
    from app.services.customer_matching import normalize_email, normalize_phone

    e, ph = normalize_email(email), normalize_phone(phone)
    if not e and not ph:
        return False, "no_recipient_address"
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)

        async def leads_of_customer(cid):
            return list((await session.execute(select(Lead).where(Lead.tenant_id == tenant_id, Lead.customer_id == cid))).scalars())

        if lead_id is not None or customer_id is not None:
            lead = await session.get(Lead, lead_id) if lead_id is not None else None
            if lead_id is not None and (lead is None or lead.tenant_id != tenant_id):
                return False, "bound_identity_not_found"
            customer = await session.get(Customer, customer_id) if customer_id is not None else None
            if customer_id is not None and (customer is None or customer.tenant_id != tenant_id):
                return False, "bound_identity_not_found"
            if lead is not None and customer is not None and lead.customer_id != customer.id:
                return False, "bound_identity_conflict"
            if customer is None and lead is not None and lead.customer_id is not None:
                customer = await session.get(Customer, lead.customer_id)
            own_emails = {normalize_email(x) for x in ((lead.email if lead else None), (customer.email if customer else None)) if x}
            own_phones = {normalize_phone(x) for x in ((lead.phone if lead else None), (customer.phone if customer else None)) if x}
            if (e and e not in own_emails) or (ph and ph not in own_phones):
                return False, "recipient_not_bound_patient"
            leads = [lead] if (lead is not None and customer is None) else (await leads_of_customer(customer.id) if customer is not None else [])
            if lead is not None and lead not in leads:
                leads.append(lead)
            if not leads:
                return False, "no_lead_evidence"
        else:
            lead_keys = ([Lead.email == e] if e else []) + ([Lead.phone_normalized == ph] if ph else [])
            cust_keys = ([Customer.email == e] if e else []) + ([Customer.phone_normalized == ph] if ph else [])
            leads = list((await session.execute(select(Lead).where(Lead.tenant_id == tenant_id, or_(*lead_keys)))).scalars())
            cust_ids = list((await session.execute(select(Customer.id).where(Customer.tenant_id == tenant_id, or_(*cust_keys)))).scalars())
            if cust_ids:
                leads += list((await session.execute(select(Lead).where(Lead.tenant_id == tenant_id, Lead.customer_id.in_(cust_ids)))).scalars())
            patients = {("c", x) for x in cust_ids} | {("c", l.customer_id) if l.customer_id else ("l", l.id) for l in leads}
            if not patients:
                return False, "recipient_matches_no_patient"
            if len(patients) > 1:
                return False, "ambiguous_recipient"
        seen: set = set()
        for lead in leads:
            if lead.id in seen:
                continue
            seen.add(lead.id)
            state = await halla_consent.state_for(session, tenant_id, halla_lead_id=lead.external_id, lead_id=lead.id)
            if not state.allows(CONTACT):
                return False, "no_contact_consent"
    return True, "ok"


async def recipient_may_be_contacted(session_factory, tenant_id: uuid.UUID, *, email: str | None = None, phone: str | None = None) -> bool:
    """Unbound contact check (kept for callers that only have an address): see `contact_decision`."""
    return (await contact_decision(session_factory, tenant_id, email=email, phone=phone))[0]


async def is_genuine_pending_invitee(session_factory, tenant_id: uuid.UUID, invite_id: uuid.UUID | None, email: str | None) -> bool:
    """True only for a real, unexpired PENDING team invitation of THIS tenant addressed to exactly `email`."""
    if invite_id is None or not email:
        return False
    from datetime import UTC, datetime

    from app.db.session import set_tenant_context
    from app.models.user import InviteStatus, TeamInvite

    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        invite = await session.get(TeamInvite, invite_id)
    if invite is None or invite.tenant_id != tenant_id or str(invite.status) != str(InviteStatus.PENDING):
        return False
    exp = invite.expires_at if invite.expires_at.tzinfo else invite.expires_at.replace(tzinfo=UTC)
    return exp > datetime.now(UTC) and invite.email.strip().lower() == email.strip().lower()
