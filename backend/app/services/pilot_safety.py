"""Klaros-side safety profiles for the two Halla pilots (Medical Tourism, Dropshipping), usable on the Halla event path.

A profile is chosen per Klaros tenant (stored as `safety_profile` in the Halla connection's non-secret metadata by scripts/start.py). It does two
things, both deterministic and free of any model:

  * `workforce_context`   the escalation triggers and rules Klaros sends as the tenant's Halla workforce configuration
  * `assess`              classifies the summary / outcome text of a Halla conversation; anything that needs a person (an emergency,
                          a diagnosis or prescription request, an outcome guarantee, an unknown provider; a payment or refund dispute,
                          a chargeback, fraud, a delivery guarantee, an invented fact, a prohibited product ...) is routed to a human

What this can and cannot do: Klaros enforces these rules on the text it RECEIVES AND STORES. It does not control what Halla's live agents say
on a call (Halla's workforce configuration has no safety-policy field). The text is never logged, stored or echoed by this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.services import dropshipping_safety, medical_tourism_safety

MEDICAL_TOURISM = "medical_tourism"
DROPSHIPPING = "dropshipping"

MEDICAL_ESCALATION_TRIGGERS: list[str] = [
    "Any emergency or urgent medical situation (direct the person to local emergency services first, then hand over to a person)",
    "A complex or clinical medical question",
    "Any request for a diagnosis, medication advice or a guaranteed outcome",
    "A question about whether to have, change, delay or stop a treatment (treatment decisions are for the patient's own clinician)",
    "A question about a specific doctor, hospital or price that is not in the verified knowledge base",
    "A question the verified knowledge base does not cover",
    "The patient asks to speak to a person",
    "The patient says they do not want to be contacted",
]

MEDICAL_BOOKING_RULES: list[str] = [
    "Collect business information only: never ask for diagnoses, medical records or medication details.",
    "Ask for consent before recording anything about the patient's health, and before any follow-up contact.",
    "Never promise a provider, price, date or availability that a person has not confirmed.",
    "Record an appointment as a request; a person confirms the provider and the time.",
]

DROPSHIPPING_ESCALATION_TRIGGERS: list[str] = [
    "A payment dispute, a chargeback or any mention of the customer's bank",
    "A refund dispute, or any request for a refund decision (only a person decides refunds)",
    "Suspected fraud or an unauthorised payment",
    "A request to guarantee or promise a delivery date",
    "A request to invent or confirm a price, discount, stock level or product specification that is not in verified business data",
    "A request for a product the business does not sell, or a prohibited product",
    "A question about the status of an order, a delivery or fulfillment (no order system is connected: only a person can answer it)",
    "A question the verified knowledge base does not cover",
    "A legal threat or a threat of public complaint",
    "The customer asks to speak to a person",
    "The customer says they do not want to be contacted",
]

DROPSHIPPING_BOOKING_RULES: list[str] = [
    "Collect business information only: never ask for card numbers, bank details or passwords.",
    "State a price, stock level, delivery window or specification only if it is recorded in verified business data; otherwise say a team member will confirm.",
    "Never offer or invent a discount, and never promise or guarantee a delivery date.",
    "Never state a payment status or decide a refund; hand those to a person.",
    "Ask for consent before any follow-up contact.",
]



MEDICAL_QUALIFICATION_FIELDS: list[str] = [
    "Treatment of interest",
    "Preferred destination country",
    "Travel timeframe",
    "Insurance or self-pay",
    "Budget range (only if the patient wishes to say)",
    "Preferred language",
    "Preferred contact time and channel",
    "Consent to be contacted",
]

DROPSHIPPING_QUALIFICATION_FIELDS: list[str] = [
    "Product of interest",
    "Quantity",
    "Destination country",
    "Preferred contact method",
    "Consent to be contacted",
]


@dataclass(frozen=True)
class Assessment:
    requires_human: bool
    category: str  # a fixed category name, never any of the text
    matched_rules: tuple[str, ...] = ()


@dataclass(frozen=True)
class SafetyProfile:
    key: str
    classify: Callable[..., Any]
    escalation_triggers: list[str]
    booking_rules: list[str]
    qualification_fields: list[str]
    human_flags: tuple[str, ...] = ()  # ordinary-question flags that this profile still hands to a person (the category is named below)
    requires_consent_evidence: bool = False  # a lead Halla creates for this tenant is only stored when the event carries valid consent evidence


_FLAG_CATEGORY = {"order_status_question": "ORDER_STATUS_REQUEST"}

PROFILES: dict[str, SafetyProfile] = {
    MEDICAL_TOURISM: SafetyProfile(MEDICAL_TOURISM, medical_tourism_safety.classify_many, MEDICAL_ESCALATION_TRIGGERS, MEDICAL_BOOKING_RULES, MEDICAL_QUALIFICATION_FIELDS, requires_consent_evidence=True),
    DROPSHIPPING: SafetyProfile(DROPSHIPPING, dropshipping_safety.classify_many, DROPSHIPPING_ESCALATION_TRIGGERS, DROPSHIPPING_BOOKING_RULES, DROPSHIPPING_QUALIFICATION_FIELDS, ('order_status_question',)),
}


def get_profile(key: str | None) -> SafetyProfile | None:
    return PROFILES.get(key or "")


def assess(profile: SafetyProfile, *texts: str | None) -> Assessment:
    result = profile.classify([t for t in texts if t])
    if not result.requires_human:
        hit = next((f for f in profile.human_flags if f in (getattr(result, "flags", ()) or ())), None)
        if hit:  # e.g. Dropshipping: "where is my order?" — there is no order system, so a person answers it
            return Assessment(True, _FLAG_CATEGORY.get(hit, hit.upper()), (hit,))
    return Assessment(bool(result.requires_human), result.category.value, tuple(getattr(result, "matched_rules", ()) or ()))


def workforce_context(profile_key: str) -> dict[str, Any]:
    p = PROFILES[profile_key]
    return {"escalation_triggers": list(p.escalation_triggers), "booking_rules": list(p.booking_rules), "qualification_fields": list(p.qualification_fields)}


async def profile_in_session(session, tenant_id) -> SafetyProfile | None:
    """The safety profile of this Klaros tenant: the non-secret `safety_profile` stored with its Halla connection by scripts/start.py. A tenant
    with no profile is unchanged (generic behaviour). `session` must already carry the tenant context."""
    from sqlalchemy import select

    from app.models.integration import IntegrationConnection

    conn = (await session.execute(select(IntegrationConnection).where(IntegrationConnection.tenant_id == tenant_id, IntegrationConnection.provider == "halla"))).scalar_one_or_none()
    meta = conn.connection_metadata if conn is not None and isinstance(conn.connection_metadata, dict) else {}
    return get_profile(meta.get("safety_profile"))


async def profile_for_tenant(session_factory, tenant_id) -> SafetyProfile | None:
    from app.db.session import set_tenant_context

    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        return await profile_in_session(session, tenant_id)
