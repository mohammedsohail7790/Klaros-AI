"""Halla consent evidence: record it, and derive what Klaros may currently do.

The three scopes are independent and none implies another:
  contact                    -> Klaros may reach out to the person (e.g. an outbound call)
  store_personal_data        -> Klaros may keep a lead with the person's name / phone / email
  store_medical_information  -> Klaros may keep treatment / health-related details

State rule (fail closed): the NEWEST valid evidence (by Halla's `recorded_at`) defines the whole current state. A granted event grants
exactly its listed scopes; every scope it omits is NOT granted -- including one granted earlier, because Halla's producer lists only
currently-granted scopes and has no standalone withdrawal event, so an omitted scope is the only way a later withdrawal can show up. A
declined/withdrawn event (granted=false) grants nothing. Older evidence never overrides newer evidence (a stale or replayed event is kept
as history and changes nothing). If two records share the newest timestamp the more restrictive wins (scopes are intersected; any
granted=false means nothing is granted). No evidence at all => nothing is granted.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import or_, select, update

from app.integrations.workforce.halla_webhook import ConsentEvidence
from app.models.halla_consent import HallaConsentEvidence

CONTACT, STORE_PERSONAL, STORE_MEDICAL = "contact", "store_personal_data", "store_medical_information"


@dataclass(frozen=True)
class ConsentState:
    granted: frozenset[str]
    has_evidence: bool

    def allows(self, scope: str) -> bool:
        return scope in self.granted


NO_CONSENT = ConsentState(frozenset(), False)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)  # SQLite returns naive datetimes; PostgreSQL returns aware ones


def derive_state(rows: Iterable[HallaConsentEvidence]) -> ConsentState:
    rows = list(rows)
    if not rows:
        return NO_CONSENT
    newest = max(_utc(r.recorded_at) for r in rows)
    tied = [r for r in rows if _utc(r.recorded_at) == newest]
    granted: set[str] | None = None
    for r in tied:
        mine = set(r.scopes) if r.granted else set()
        granted = mine if granted is None else granted & mine
    return ConsentState(frozenset(granted or ()), True)


async def record(session, tenant_id: uuid.UUID, *, event_id: str, event_type: str, halla_lead_id: str | None, lead_id: uuid.UUID | None, evidence: ConsentEvidence) -> bool:
    """Append one evidence row. Idempotent on (tenant, Halla event id): a redelivery adds nothing. Returns True when a row was added."""
    exists = (await session.execute(select(HallaConsentEvidence.id).where(HallaConsentEvidence.tenant_id == tenant_id, HallaConsentEvidence.halla_event_id == event_id))).first()
    if exists is not None:
        return False
    session.add(
        HallaConsentEvidence(
            tenant_id=tenant_id, halla_event_id=event_id[:255], event_type=event_type[:32], halla_lead_id=halla_lead_id, lead_id=lead_id, granted=evidence.granted,
            scopes=list(evidence.scopes), method=evidence.method, wording_version=evidence.wording_version, recorded_at=evidence.recorded_at,
        )
    )
    await session.flush()
    return True


async def link_lead(session, tenant_id: uuid.UUID, halla_lead_id: str | None, lead_id: uuid.UUID) -> None:
    """Attach the Klaros lead to evidence that arrived before the lead existed. Only fills an empty link; never re-points one."""
    if halla_lead_id:
        await session.execute(
            update(HallaConsentEvidence)
            .where(HallaConsentEvidence.tenant_id == tenant_id, HallaConsentEvidence.halla_lead_id == halla_lead_id, HallaConsentEvidence.lead_id.is_(None))
            .values(lead_id=lead_id)
        )


async def state_for(session, tenant_id: uuid.UUID, *, halla_lead_id: str | None = None, lead_id: uuid.UUID | None = None) -> ConsentState:
    keys = []
    if halla_lead_id:
        keys.append(HallaConsentEvidence.halla_lead_id == halla_lead_id)
    if lead_id:
        keys.append(HallaConsentEvidence.lead_id == lead_id)
    if not keys:
        return NO_CONSENT
    rows = (await session.execute(select(HallaConsentEvidence).where(HallaConsentEvidence.tenant_id == tenant_id, or_(*keys)))).scalars().all()
    return derive_state(rows)


class ConsentStillGrantedError(Exception):
    """Erasure is refused while the newest evidence still grants `store_personal_data` (or there is no evidence at all)."""


async def describe_for_lead(session, tenant_id: uuid.UUID, lead) -> dict:
    """Operator-facing view: what is consented NOW and why. No contact details, no wording text, no secrets."""
    state = await state_for(session, tenant_id, halla_lead_id=lead.external_id, lead_id=lead.id)
    rows = (
        await session.execute(
            select(HallaConsentEvidence)
            .where(HallaConsentEvidence.tenant_id == tenant_id, or_(HallaConsentEvidence.lead_id == lead.id, HallaConsentEvidence.halla_lead_id == (lead.external_id or "\0")))
            .order_by(HallaConsentEvidence.recorded_at.desc())
        )
    ).scalars().all()
    newest = rows[0] if rows else None
    return {
        "evidence_recorded": state.has_evidence,
        "granted_scopes": sorted(state.granted),
        "contact": state.allows(CONTACT),
        "store_personal_data": state.allows(STORE_PERSONAL),
        "store_medical_information": state.allows(STORE_MEDICAL),
        "as_of": _utc(newest.recorded_at).isoformat() if newest else None,
        "wording_version": newest.wording_version if newest else None,
        "method": newest.method if newest else None,
        "history_count": len(rows),  # the history itself is retained and never rewritten
        "note": None if state.has_evidence else "Halla has sent no consent evidence for this lead; nothing is treated as consented.",
    }


ERASED_NAME = "Erased (consent withdrawn)"


async def erase_personal_data(session, tenant_id: uuid.UUID, lead) -> dict:
    """Operator action (never automatic: no retention period has been approved). Removes the lead's contact details and free text after the
    newest evidence shows `store_personal_data` is not consented. The lead row, its ids, status and the consent history stay (the history holds
    no personal data and is the audit trail of the decision). A linked customer record is NOT touched here: it may carry other business."""
    state = await state_for(session, tenant_id, halla_lead_id=lead.external_id, lead_id=lead.id)
    if not state.has_evidence or state.allows(STORE_PERSONAL):
        raise ConsentStillGrantedError("Personal-data consent has not been withdrawn or declined for this lead.")
    lead.name = ERASED_NAME
    lead.phone = lead.phone_normalized = lead.email = lead.description = lead.location = lead.service_requested = None
    await session.flush()
    return {"erased": True, "customer_linked": lead.customer_id is not None}
