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


async def record(session, tenant_id: uuid.UUID, *, event_id: str, event_type: str, halla_lead_id: str | None, lead_id: uuid.UUID | None, evidence: ConsentEvidence,
                 source: str = "halla", actor_user_id: uuid.UUID | None = None) -> bool:
    """Append one evidence row. Idempotent on (tenant, Halla event id): a redelivery adds nothing. Returns True when a row was added."""
    exists = (await session.execute(select(HallaConsentEvidence.id).where(HallaConsentEvidence.tenant_id == tenant_id, HallaConsentEvidence.halla_event_id == event_id))).first()
    if exists is not None:
        return False
    session.add(
        HallaConsentEvidence(
            tenant_id=tenant_id, halla_event_id=event_id[:255], event_type=event_type[:32], source=source, actor_user_id=actor_user_id, halla_lead_id=halla_lead_id, lead_id=lead_id, granted=evidence.granted,
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
    keys = [HallaConsentEvidence.lead_id == lead.id] + ([HallaConsentEvidence.halla_lead_id == lead.external_id] if lead.external_id else [])
    rows = (
        await session.execute(select(HallaConsentEvidence).where(HallaConsentEvidence.tenant_id == tenant_id, or_(*keys)).order_by(HallaConsentEvidence.recorded_at.desc()))
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
ERASED_TEXT = "[erased]"
# Event text fields written by the Halla processor; scrubbed on erasure (the event rows, ids and categories stay).
_EVENT_TEXT_KEYS = ("summary", "outcome")


async def erase_personal_data(session, tenant_id: uuid.UUID, lead, actor_user_id: uuid.UUID | None = None) -> dict:
    """Operator action (never automatic: no retention period has been approved). After the newest evidence shows `store_personal_data` is not
    consented, removes the personal and health content that Klaros copied around this lead, and writes a privacy-minimising audit record (counts only).

    ERASED:  the lead's contact details and free text; its appointments' title/notes/location/service; its patient-lead health intake text;
             its voice-session caller number, transcript and engine state; summary/outcome text on its Halla events; and the linked customer
             (name, contact details, notes) **only when nothing else references that customer** (otherwise it is kept and reported).
    KEPT:    the lead row, its ids (including the Halla idempotency key, which is what stops re-creation), status, times, event rows without text, the
             consent history (it holds no personal data and is the audit trail), and any invoice/quote/job/etc. that references the customer (financial and
             contractual records follow their own retention rules: owner decision)."""
    from sqlalchemy import func, select, update

    from app.db.base import Base
    from app.models.actor import ActorType
    from app.models.audit_log import AuditLog
    from app.models.crm import Appointment, Customer, CustomerNote, Lead
    from app.models.event import Event
    from app.models.medical_tourism import PatientLead
    from app.models.voice import CallSession

    state = await state_for(session, tenant_id, halla_lead_id=lead.external_id, lead_id=lead.id)
    if not state.has_evidence or state.allows(STORE_PERSONAL):
        raise ConsentStillGrantedError("Personal-data consent has not been withdrawn or declined for this lead.")

    report: dict = {"erased": True, "lead": 1}
    lead.name = ERASED_NAME
    lead.phone = lead.phone_normalized = lead.email = lead.description = lead.location = lead.service_requested = None

    appts = (await session.execute(select(Appointment).where(Appointment.tenant_id == tenant_id, Appointment.lead_id == lead.id))).scalars().all()
    for a in appts:
        a.title, a.notes, a.location, a.service = "Appointment (erased)", None, None, None
    report["appointments"] = len(appts)

    pls = (await session.execute(select(PatientLead).where(PatientLead.tenant_id == tenant_id, PatientLead.lead_id == lead.id))).scalars().all()
    for pl in pls:
        pl.medical_history_summary = pl.insurance_notes = None
    report["patient_leads"] = len(pls)

    calls = (await session.execute(select(CallSession).where(CallSession.tenant_id == tenant_id, CallSession.lead_id == lead.id))).scalars().all()
    for c in calls:
        c.caller_number, c.transcript, c.engine_state = None, [], {}
    report["call_sessions"] = len(calls)

    events = (await session.execute(select(Event).where(Event.tenant_id == tenant_id, Event.entity_type == "lead", Event.entity_id == lead.id, Event.event_type.like("halla.%")))).scalars().all()
    scrubbed = 0
    for e in events:
        payload = dict(e.payload or {})
        if any(payload.get(k) for k in _EVENT_TEXT_KEYS):
            for k in _EVENT_TEXT_KEYS:
                if k in payload:
                    payload[k] = None
            e.payload = payload
            scrubbed += 1
    report["events_scrubbed"] = scrubbed

    report["customer"] = "none"
    if lead.customer_id is not None:
        cid, blockers = lead.customer_id, []
        for name, table in Base.metadata.tables.items():
            if name in ("customers", "customer_notes") or "customer_id" not in table.c or "tenant_id" not in table.c:
                continue
            q = select(func.count()).select_from(table).where(table.c.customer_id == cid, table.c.tenant_id == tenant_id)
            if name == "leads":
                q = q.where(table.c.id != lead.id)
            elif name == "appointments":
                q = q.where((table.c.lead_id.is_(None)) | (table.c.lead_id != lead.id))
            if (await session.execute(q)).scalar():
                blockers.append(name)
        if blockers:
            report["customer"] = "kept"
            report["customer_kept_because_referenced_by"] = sorted(blockers)
        else:
            customer = await session.get(Customer, cid)
            if customer is not None and customer.tenant_id == tenant_id:
                customer.name, customer.company_name, customer.email, customer.phone, customer.phone_normalized = ERASED_NAME, None, None, None, None
                customer.address = customer.city = customer.state = customer.postal_code = customer.notes = None
                await session.execute(update(CustomerNote).where(CustomerNote.tenant_id == tenant_id, CustomerNote.customer_id == cid).values(body=ERASED_TEXT))
                report["customer"] = "erased"

    session.add(AuditLog(
        tenant_id=tenant_id, actor_type=ActorType.USER if actor_user_id else ActorType.SYSTEM, actor_id=actor_user_id, action="lead.personal_data_erased",
        entity_type="lead", entity_id=lead.id, input_summary={k: v for k, v in report.items() if k != "erased"}, result="success",
    ))
    await session.flush()
    return report


async def record_snapshot(session, tenant_id: uuid.UUID, lead_id: uuid.UUID, *, granted_scopes, source: str, wording_version: str | None, actor_user_id: uuid.UUID | None) -> None:
    """Append a COMPLETE-STATE decision from a non-Halla source (operator attestation / web form): exactly `granted_scopes` are consented, every other
    scope is not. An empty set records a withdrawal of everything. The newest record wins, like every other evidence record."""
    from app.integrations.workforce.halla_webhook import CONSENT_SCOPES

    scopes = tuple(s for s in CONSENT_SCOPES if s in set(granted_scopes))
    method = "web_form" if source == "web_form" else "operator_attested"
    evidence = ConsentEvidence(granted=bool(scopes), scopes=scopes or tuple(CONSENT_SCOPES), method=method, wording_version=(wording_version or "n/a")[:64], recorded_at=datetime.now(timezone.utc))
    await record(session, tenant_id, event_id=f"{source}:{uuid.uuid4()}", event_type="consent.snapshot", halla_lead_id=None, lead_id=lead_id, evidence=evidence, source=source, actor_user_id=actor_user_id)
