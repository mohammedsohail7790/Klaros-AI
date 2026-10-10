"""Applies a VERIFIED Halla webhook event to Klaros' existing operating layer.

Klaros stays the operational source of truth. This module only maps what Halla reported onto
structures that already exist — the lead (status / qualification), the event bus (which the
existing automation engine already dispatches workflows from), and the appointment calendar — and
never invents a state Halla did not supply. It runs after the receiver has verified the signature,
matched the tenant to the stored connection, and recorded the event id for idempotency, so it only
ever sees authentic, tenant-resolved, first-time events (and is itself safe to re-run: every write is
keyed on the Halla event id or on the Halla appointment id).

Ownership decision for appointments: Halla EXECUTES the bookings it makes (its own calendar); Klaros
MIRRORS each one as an `Appointment` with `external_provider="halla"` / `external_id=<Halla id>` so the
operating layer can see it. Klaros never pushes that appointment back to Halla, and never publishes the
core `appointment.created` event for it (that would trigger Klaros' own calendar sync and double-book).

Transcripts are not copied: only the summary, the outcome, the qualification and Halla's call id.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.events.bus import EventBus
from app.integrations.workforce import halla_webhook as hw
from app.models.crm import Appointment, AppointmentStatus, Customer, CustomerStatus, Lead, LeadStatus, QualificationStatus
from app.models.event import Event, EventType
from app.services import halla_consent, pilot_safety
from app.services.consent_gate import ConsentClaim, ConsentRequiredError, tenant_requires_consent
from app.services.customer_matching import find_matching_customer, normalize_email, normalize_phone

logger = structlog.get_logger(__name__)

PROVIDER = "halla"
DEFAULT_APPOINTMENT_MINUTES = 30

_TYPE_TO_INTERNAL = {
    "call.started": EventType.HALLA_INTERACTION_STARTED,
    "call.completed": EventType.HALLA_INTERACTION_COMPLETED,
    "lead.qualified": EventType.HALLA_LEAD_QUALIFIED,
    "lead.escalated": EventType.HALLA_LEAD_ESCALATED,
    "appointment.confirmed": EventType.HALLA_APPOINTMENT_CONFIRMED,
    "appointment.rescheduled": EventType.HALLA_APPOINTMENT_RESCHEDULED,
    "appointment.cancelled": EventType.HALLA_APPOINTMENT_CANCELLED,
}

_OPEN = (LeadStatus.NEW, LeadStatus.CONTACTED)
_CLOSED = (LeadStatus.BOOKED, LeadStatus.CONVERTED, LeadStatus.LOST)


def apply_qualification(lead: Lead, qualification: str | None) -> bool:
    """Record one of Halla's four qualification states on the lead. Returns True if anything changed.

    Two separate things, deliberately not coupled:
      * `lead.qualification_status` is a RECORD of what Halla reported. It is recorded for every lead, including one that has
        already moved on (booked, converted, lost) — Halla's events can arrive in any order, and an `appointment.confirmed`
        that lands before its `lead.qualified` must not leave the qualification stuck at PENDING.
      * `lead.status` is the pipeline position. A machine only ever moves it FORWARD out of NEW/CONTACTED; it is never
        pulled back from QUALIFIED/BOOKED/CONVERTED/LOST.
    `unknown` (or nothing) never changes anything, and a lead already known to be qualified is never marked not-qualified by
    a machine (a late or stale `not_qualified` cannot undo a qualification, or a booking that followed it)."""
    if qualification in (None, "unknown"):
        return False
    closed = lead.status in _CLOSED
    changed = False
    if qualification == "qualified":
        if lead.qualification_status != QualificationStatus.QUALIFIED:
            lead.qualification_status, changed = QualificationStatus.QUALIFIED, True
        if lead.status in _OPEN:
            lead.status, changed = LeadStatus.QUALIFIED, True
    elif qualification == "not_qualified":
        if lead.status == LeadStatus.QUALIFIED or (closed and lead.qualification_status == QualificationStatus.QUALIFIED):
            return False
        if lead.qualification_status != QualificationStatus.UNQUALIFIED:
            lead.qualification_status, changed = QualificationStatus.UNQUALIFIED, True
        if lead.status in _OPEN:
            lead.status, changed = LeadStatus.UNQUALIFIED, True
    elif qualification == "needs_human_review":
        if lead.qualification_status != QualificationStatus.REQUIRES_HUMAN:
            lead.qualification_status, changed = QualificationStatus.REQUIRES_HUMAN, True
    return changed


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        t = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class HallaEventProcessor:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def process(self, tenant_id: uuid.UUID, env: hw.HallaEnvelope, bus: EventBus) -> dict[str, Any]:
        data = env.data
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            profile = await pilot_safety.profile_in_session(session, tenant_id)
            gated = await tenant_requires_consent(self._session_factory, tenant_id)  # consent evidence decides what may be stored (profile OR vertical)
            lead = await self._resolve_lead(session, tenant_id, data)
            if gated and env.type in ("lead.created", "lead.updated"):
                await self._record_consent(session, tenant_id, env, data, lead)
            if lead is None and (env.type == "lead.created" or (gated and env.type == "lead.updated")):
                lead = await self._create_lead_from_halla(tenant_id, data, bus, session, gated)
                if lead is not None and gated:
                    await halla_consent.link_lead(session, tenant_id, hw.halla_lead_id(data), lead.id)
            if lead is None:
                # Nothing in Klaros to attach this to. Klaros never creates a lead from a bare phone number.
                logger.info("halla_event_unresolved", event_type=env.type, event_id=env.id)
                await session.commit()  # keeps any consent evidence recorded above (it holds no personal data)
                return {"handled": False, "note": "consent not established" if gated and env.type in ("lead.created", "lead.updated") else "no matching Klaros lead"}

            await self._link(session, tenant_id, lead, data)
            consent_state = None
            if gated:
                consent_state = await halla_consent.state_for(session, tenant_id, halla_lead_id=lead.external_id or hw.halla_lead_id(data), lead_id=lead.id)
            events: list[tuple[EventType, dict[str, Any]]] = []
            q = hw.qualification(data)
            summary, outcome, call = hw.summary_of(data), hw.outcome_of(data), hw.call_id(data)
            # Tenant safety profile (Medical Tourism / Dropshipping): if the conversation's own text needs a person, Halla's "qualified" is not
            # applied and no qualified-lead workflow is triggered; the lead goes to a human with the CATEGORY only (never the text).
            texts = hw.safety_texts(data)
            safety = pilot_safety.assess(profile, *texts) if profile is not None and texts else None
            flagged = bool(safety and safety.requires_human)
            if flagged:
                logger.warning("halla_safety_escalation", event_id=env.id, event_type=env.type, profile=profile.key, category=safety.category)
                q = None
            if consent_state is not None and consent_state.has_evidence and not consent_state.allows(halla_consent.STORE_PERSONAL) and not flagged and env.type != "lead.escalated":
                # The newest evidence says personal-data storage is NOT (or no longer) consented. The lead is frozen: Halla's later events do not
                # change it, qualify it, book it or add text to it. Safety escalations still reach a person. Nothing is erased automatically
                # (see HallaConsentService.erase_personal_data); a person decides.
                logger.warning("halla_event_ignored_consent_not_granted", event_id=env.id, event_type=env.type)
                await session.commit()
                return {"handled": False, "note": "consent not granted for this lead"}
            stale = env.type in ("call.completed", "lead.qualified") and await self._qualification_is_stale(session, tenant_id, lead.id, env)
            # Free text from a Medical Tourism conversation may contain health information: it is kept only while the newest evidence grants
            # `store_medical_information`. (No evidence, or any other combination of scopes => not retained.) Other tenants are unchanged.
            keep_text = not flagged and (not gated or (consent_state is not None and consent_state.allows(halla_consent.STORE_MEDICAL)))
            base = {"halla_event_id": env.id, "occurred_at": env.timestamp, "interaction_id": call, "channel": "voice" if env.type.startswith("call.") else None,
                    "summary": summary if keep_text else None, "outcome": outcome if keep_text else None, "simulated": False}  # sensitive text is not stored

            if env.type in ("call.started",):
                events.append((EventType.HALLA_INTERACTION_STARTED, base))
            elif env.type == "call.completed":
                events.append((EventType.HALLA_INTERACTION_COMPLETED, {**base, "qualification": q}))
                if not stale:
                    apply_qualification(lead, q)
                if hw.escalated(data):
                    apply_qualification(lead, "needs_human_review")
                    events.append((EventType.HALLA_LEAD_ESCALATED, {**base, "escalated_from": "call.completed"}))
            elif env.type == "lead.qualified":
                if flagged:
                    pass  # handed to a person below; a qualified-lead event/workflow must not fire for it
                elif stale:
                    # Delivered late: Halla already told us a newer qualification outcome. Never let a stale event win.
                    logger.info("halla_stale_qualification_ignored", event_id=env.id)
                else:
                    events.append((EventType.HALLA_LEAD_QUALIFIED, {**base, "qualification": q or "qualified"}))
                    apply_qualification(lead, q or "qualified")
            elif env.type == "lead.escalated":
                events.append((EventType.HALLA_LEAD_ESCALATED, {**base, "category": safety.category, "safety": True} if flagged else base))
                apply_qualification(lead, "needs_human_review")
            elif env.type in ("appointment.confirmed", "appointment.rescheduled", "appointment.cancelled"):
                booked = await self._mirror_appointment(session, tenant_id, lead, env.type, data)
                events.append((_TYPE_TO_INTERNAL[env.type], {**base, "appointment_mirrored": booked}))
            # lead.created / lead.updated: only the link above (Klaros stays the source of truth).
            if flagged and env.type != "lead.escalated":
                apply_qualification(lead, "needs_human_review")
                events.append((EventType.HALLA_LEAD_ESCALATED, {**base, "summary": None, "outcome": None, "category": safety.category, "safety": True, "escalated_from": env.type}))

            lead_id = lead.id
            await session.commit()

        for etype, payload in events:
            # Keyed on Halla's event id: the same delivery can never publish — or trigger a workflow — twice.
            # An escalation is keyed on the CALL when there is one: Halla reports it both inside `call.completed` and as a
            # separate `lead.escalated`, in either order, and the two must be one escalation (one workflow run), not two.
            key = f"halla:{env.id}:{etype.value}"
            if etype == EventType.HALLA_LEAD_ESCALATED and call:
                key = f"halla:escalation:{lead_id}:{call}"
            await bus.publish(
                tenant_id=tenant_id, event_type=etype, source="halla", entity_type="lead", entity_id=lead_id,
                payload={k: v for k, v in payload.items() if v is not None},
                idempotency_key=key,
            )
        return {"handled": True, "lead_id": str(lead_id), "note": env.type}

    # ----------------------------------------------------------------- helpers

    async def _qualification_is_stale(self, session, tenant_id: uuid.UUID, lead_id: uuid.UUID, env: hw.HallaEnvelope) -> bool:
        """True when a qualification outcome that Halla stamped LATER than this event has already been recorded for the
        lead — i.e. this one arrived out of order (a retry can deliver `lead.qualified` after `call.completed`). Ordering
        uses Halla's own envelope timestamp, never arrival time; an event with no usable timestamp is applied as received."""
        mine = _parse_ts(env.timestamp)
        if mine is None:
            return False
        rows = (
            await session.execute(
                select(Event.payload)
                .where(
                    Event.tenant_id == tenant_id, Event.entity_type == "lead", Event.entity_id == lead_id,
                    Event.event_type.in_((EventType.HALLA_LEAD_QUALIFIED.value, EventType.HALLA_INTERACTION_COMPLETED.value)),
                )
                .order_by(Event.created_at.desc())
                .limit(50)
            )
        ).scalars().all()
        for payload in rows:
            theirs = _parse_ts((payload or {}).get("occurred_at"))
            if theirs is not None and (payload or {}).get("qualification") and theirs > mine:
                return True
        return False

    async def _resolve_lead(self, session, tenant_id: uuid.UUID, data: dict[str, Any]) -> Lead | None:
        kid = hw.klaros_lead_id(data)
        if kid:
            try:
                lead = (await session.execute(select(Lead).where(Lead.id == uuid.UUID(kid), Lead.tenant_id == tenant_id))).scalar_one_or_none()
            except ValueError:
                lead = None
            if lead is not None:
                return lead
        hid = hw.halla_lead_id(data)
        if hid:
            return (
                await session.execute(
                    select(Lead).where(Lead.tenant_id == tenant_id, Lead.external_provider == PROVIDER, Lead.external_id == hid)
                )
            ).scalar_one_or_none()
        return None

    async def _link(self, session, tenant_id: uuid.UUID, lead: Lead, data: dict[str, Any]) -> None:
        hid = hw.halla_lead_id(data)
        if not hid or lead.external_id is not None:
            return
        # Another Klaros lead already carries this Halla id: never steal it (the unique constraint would also
        # turn one odd event into an endless failing redelivery).
        taken = (
            await session.execute(
                select(Lead.id).where(Lead.tenant_id == tenant_id, Lead.external_provider == PROVIDER, Lead.external_id == hid)
            )
        ).first()
        if taken is None:
            lead.external_provider, lead.external_id = PROVIDER, hid

    async def _record_consent(self, session, tenant_id: uuid.UUID, env: hw.HallaEnvelope, data: dict[str, Any], lead: Lead | None) -> None:
        if "consent" not in data:
            return  # no evidence on this event; whatever is stored stays as it was (never inferred, never cleared)
        evidence = hw.consent_evidence(data)
        if evidence is None:
            logger.warning("halla_consent_evidence_invalid", event_id=env.id, event_type=env.type)  # the malformed value is never logged
            return
        await halla_consent.record(
            session, tenant_id, event_id=env.id, event_type=env.type, halla_lead_id=hw.halla_lead_id(data), lead_id=lead.id if lead is not None else None, evidence=evidence
        )
        await session.commit()  # evidence is durable before any lead is written
        await set_tenant_context(session, tenant_id)

    async def _create_lead_from_halla(self, tenant_id: uuid.UUID, data: dict[str, Any], bus: EventBus, session, gated: bool = False) -> Lead | None:
        """`lead.created` with no Klaros id: Klaros' existing lead intake policy — idempotent on the Halla lead id,
        never merged or duplicated on a phone number alone. For a profile that requires consent evidence the lead is stored only while the
        newest evidence grants `store_personal_data`; the treatment is kept only if it also grants `store_medical_information`."""
        hid, facts = hw.halla_lead_id(data), hw.lead_facts(data)
        if not hid or not facts.name or not (facts.phone or facts.email):
            return None
        service = facts.service
        state = None
        if gated:
            state = await halla_consent.state_for(session, tenant_id, halla_lead_id=hid)
            if not state.allows(halla_consent.STORE_PERSONAL):
                # Halla did not establish consent to keep personal data (or it was declined / withdrawn / superseded): nothing is stored.
                logger.warning("halla_lead_refused_no_consent_evidence", halla_lead_id=hid, has_evidence=state.has_evidence)
                return None
            if not state.allows(halla_consent.STORE_MEDICAL):
                service = None
        try:
            lead, _ = await self._create(tenant_id, bus, facts, service, hid, state)
        except ConsentRequiredError:
            return None
        return await session.get(Lead, lead.id)

    async def _create(self, tenant_id, bus, facts, service, hid, state):
        from app.services.lead_service import CreateLeadInput, LeadService

        return await LeadService(self._session_factory, bus).create_lead(
            tenant_id,
            CreateLeadInput(name=facts.name, source="VOICE", phone=facts.phone, email=facts.email, source_detail="halla",
                            service_requested=service, idempotency_key=f"halla-lead-{hid}",
                            consent=ConsentClaim(frozenset(state.granted), "halla", recorded=True) if state is not None else None),
        )

    async def _mirror_appointment(self, session, tenant_id: uuid.UUID, lead: Lead, etype: str, data: dict[str, Any]) -> bool:
        facts = hw.appointment_facts(data)
        if facts is None:
            return False
        appt = (
            await session.execute(
                select(Appointment).where(Appointment.tenant_id == tenant_id, Appointment.external_provider == PROVIDER, Appointment.external_id == facts.external_id)
            )
        ).scalar_one_or_none()

        if etype == "appointment.cancelled":
            if appt is None:
                return False
            appt.status = AppointmentStatus.CANCELLED
            return True

        if appt is None:
            if facts.start_time is None:
                return False  # cannot invent a time Halla did not give
            customer = await self._customer_for(session, tenant_id, lead)
            end = facts.end_time or facts.start_time + timedelta(minutes=DEFAULT_APPOINTMENT_MINUTES)
            session.add(
                Appointment(
                    tenant_id=tenant_id, lead_id=lead.id, customer_id=customer.id,
                    title=f"{facts.service or lead.service_requested or 'Appointment'} — {lead.name}"[:255],
                    service=facts.service or lead.service_requested, start_time=facts.start_time, end_time=end,
                    status=AppointmentStatus.CONFIRMED, external_provider=PROVIDER, external_id=facts.external_id,
                )
            )
        else:
            if facts.start_time is not None:
                appt.end_time = facts.end_time or facts.start_time + (appt.end_time - appt.start_time)
                appt.start_time = facts.start_time
            appt.status = AppointmentStatus.CONFIRMED
        if lead.status in (LeadStatus.NEW, LeadStatus.CONTACTED, LeadStatus.QUALIFIED):
            lead.status = LeadStatus.BOOKED
        return True

    async def _customer_for(self, session, tenant_id: uuid.UUID, lead: Lead) -> Customer:
        if lead.customer_id is not None:
            customer = await session.get(Customer, lead.customer_id)
            if customer is not None:
                return customer
        customer = await find_matching_customer(session, tenant_id=tenant_id, email=lead.email, phone=lead.phone)
        if customer is None:
            customer = Customer(
                tenant_id=tenant_id, name=lead.name, email=normalize_email(lead.email), phone=lead.phone,
                phone_normalized=normalize_phone(lead.phone), status=CustomerStatus.ACTIVE,
            )
            session.add(customer)
            await session.flush()
        lead.customer_id = customer.id
        return customer
