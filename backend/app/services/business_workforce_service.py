"""AI workforce (Klaros side of the Halla boundary) and the lead operating board.

* `setup()` — the workforce status exactly as its adapter reports it, the Klaros-built
  business context an adapter would send, the setup steps and what each member could do.
* `ingest()` — a typed workforce event arrives (tenant from the authenticated caller,
  never from the event), is published on the existing event bus, and updates the lead.
* `lead_interaction()` / `lead_board()` — the AI-interaction state of leads, derived only
  from recorded events and the truth about the workforce. No event is ever invented.

Generic: this module never names a business type or vertical.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.events.bus import EventBus
from app.integrations.workforce import WORKFORCE_CAPABILITIES, dev_simulator_enabled, get_workforce_integration
from app.integrations.workforce.context import build_context_pack
from app.integrations.workforce.events import (
    EVENT_TEXT,
    HALLA_EVENT_TYPES,
    INTERACTION_LABELS,
    HallaEventType,
    InvalidWorkforceEvent,
    WorkforceInboundEvent,
    derive_interaction_state,
)
from app.models.crm import Lead
from app.models.event import Event
from app.models.user import User
from app.models.vertical_extension import VerticalExtension
from app.services.operations_providers import get_lead_context_provider, get_workforce_context_provider
from app.services.vertical_extension_service import VerticalExtensionService


class LeadNotFoundError(Exception):
    pass


_MEMBERS = [
    ("receptionist", "AI Receptionist", "Answers incoming enquiries, captures the details and routes each request.", ("incoming_calls", "lead_qualification", "voice")),
    ("followup", "AI Follow-up Agent", "Follows up with leads and hands over to a person when one is needed.", ("outbound_communication", "outgoing_calls")),
    ("scheduling", "AI Scheduling Agent", "Runs the booking conversation and confirms appointments.", ("appointment_booking",)),
]

_GENERIC_NEXT = {
    "NEW": "Make first contact",
    "CONTACTED": "Qualify the lead",
    "QUALIFIED": "Book a consultation or appointment",
    "BOOKED": "Follow up after the appointment",
    "CONVERTED": "Follow up with the customer",
}


def lead_next_action(status: str, qualification_status: str, lead_id: uuid.UUID, module_hint: dict[str, Any] | None) -> dict[str, Any] | None:
    """Deterministic "what should happen next" for one lead. A person needed beats everything;
    then the industry module's own hint; then the generic rule for the lead's status."""
    route = f"/leads/{lead_id}"
    if status in ("LOST", "UNQUALIFIED"):
        return None
    if qualification_status == "REQUIRES_HUMAN":
        return {"text": "A person needs to contact this customer", "route": route}
    if module_hint and module_hint.get("text"):
        return {"text": module_hint["text"], "route": module_hint.get("route") or route}
    text = _GENERIC_NEXT.get(status)
    return {"text": text, "route": route} if text else None


class BusinessWorkforceService:
    def __init__(self, session_factory: async_sessionmaker, verticals: VerticalExtensionService) -> None:
        self._session_factory = session_factory
        self._verticals = verticals

    async def context_pack(self, tenant_id: uuid.UUID, business: dict[str, Any]):
        """The business context Klaros hands the workforce: the Blueprint plus whatever each enabled industry
        module registers. Built here once — the setup view previews it and the Halla integration sends it."""
        contributions: list[dict[str, Any]] = []
        for key in await self._enabled_vertical_keys(tenant_id):
            fn = get_workforce_context_provider(key)
            if fn is None:
                continue
            try:
                contributions.append(await fn(tenant_id))
            except Exception:  # noqa: BLE001 - one module's failure must not blank the page
                continue
        try:  # the tenant's safety profile contributes its escalation triggers and rules whether or not an industry module is enabled
            from app.services import pilot_safety

            profile = await pilot_safety.profile_for_tenant(self._session_factory, tenant_id)
            if profile is not None:
                contributions.append(pilot_safety.workforce_context(profile.key))
        except Exception:  # noqa: BLE001
            pass
        return build_context_pack(business, contributions)

    # ---------------------------------------------------------------- setup view

    async def setup(self, tenant_id: uuid.UUID, business: dict[str, Any]) -> dict[str, Any]:
        wf = get_workforce_integration()
        report = await wf.get_status(tenant_id)
        agent = await wf.get_agent(tenant_id)

        pack = await self.context_pack(tenant_id, business)

        connected = report.status.value == "CONNECTED"
        if report.adapter_implemented:
            connect_state, connect_detail = ("DONE", "Connected.") if connected else ("READY", "The Halla adapter is available — connect your account.")
        else:
            connect_state, connect_detail = "BLOCKED", "Integration required — Klaros has the contract, but the Halla adapter has not been built, so nothing can be connected yet."
        blocked_by_connection = "Needs a connected Halla account first."
        caps = {c.key: c for c in WORKFORCE_CAPABILITIES}
        deployed = set((agent or {}).get("capabilities", []))
        members = []
        for key, name, purpose, wanted in _MEMBERS:
            if agent and set(wanted) <= deployed and connected:
                status, label = "ACTIVE", "Active"
            elif report.adapter_implemented:
                status, label = "NOT_CONFIGURED", "Not configured"
            else:
                status, label = "AVAILABLE_THROUGH_HALLA", "Available through Halla"
            members.append({"key": key, "name": name, "purpose": purpose, "status": status, "status_label": label,
                            "capabilities": [caps[c].label for c in wanted if c in caps]})

        steps = [
            {"key": "connect", "label": "Connect Halla", "state": connect_state, "detail": connect_detail},
            {"key": "agent", "label": "Select and configure an agent", "state": "BLOCKED", "detail": blocked_by_connection},
            {"key": "context", "label": "Define business context", "state": "READY", "detail": "Prepared by Klaros from your business — review it below."},
            {"key": "qualification", "label": "Define qualification rules", "state": "READY", "detail": f"{len(pack.qualification_fields)} things Halla would ask every customer."},
            {"key": "escalation", "label": "Define escalation rules", "state": "READY", "detail": f"{len(pack.escalation_triggers)} situations that hand over to a person."},
            {"key": "booking", "label": "Define booking rules", "state": "READY", "detail": "How bookings must be made and confirmed."},
            {"key": "test", "label": "Test", "state": "BLOCKED", "detail": blocked_by_connection},
            {"key": "activate", "label": "Activate", "state": "BLOCKED", "detail": blocked_by_connection},
        ]
        if connected:
            for s in steps:
                if s["key"] in ("agent", "test", "activate"):
                    s["state"], s["detail"] = "READY", "Available now."
        return {
            "status": {
                "provider": report.provider, "status": report.status.value, "adapter_implemented": report.adapter_implemented,
                "mode": report.mode, "message": report.message, "agent_id": report.agent_id,
            },
            "channels": [],  # no channel is configured until a real adapter reports one
            "members": members,
            "steps": steps,
            "context": pack.as_dict(),
            "dev_simulator": dev_simulator_enabled(),
        }

    # -------------------------------------------------------------------- events

    async def ingest(self, tenant_id: uuid.UUID, event: WorkforceInboundEvent, bus: EventBus) -> dict[str, Any]:
        event.validate()
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            lead = (await session.execute(select(Lead).where(Lead.id == event.lead_id, Lead.tenant_id == tenant_id))).scalar_one_or_none()
            if lead is None:
                raise LeadNotFoundError("lead not found")
        published = await bus.publish(
            tenant_id=tenant_id,
            event_type=event.type.value,
            source="halla-dev" if event.simulated else "halla",
            payload={
                "interaction_id": event.interaction_id, "channel": event.channel, "summary": event.summary,
                "outcome": event.outcome, "simulated": event.simulated,
                "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
            },
            entity_type="lead",
            entity_id=event.lead_id,
            idempotency_key=f"halla:{event.interaction_id}:{event.type.value}",
        )
        # Klaros lead update — the workforce reports, Klaros records.
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            lead = (await session.execute(select(Lead).where(Lead.id == event.lead_id, Lead.tenant_id == tenant_id))).scalar_one()
            if event.type == HallaEventType.LEAD_QUALIFIED:
                lead.qualification_status = "QUALIFIED"
                if lead.status in ("NEW", "CONTACTED"):
                    lead.status = "QUALIFIED"
            elif event.type == HallaEventType.LEAD_ESCALATED:
                lead.qualification_status = "REQUIRES_HUMAN"
            await session.commit()
        interaction = await self.lead_interaction(tenant_id, event.lead_id)
        return {"event_id": str(published.id), "state": interaction["state"] if interaction else None}

    # --------------------------------------------------------------- interaction

    async def lead_interaction(self, tenant_id: uuid.UUID, lead_id: uuid.UUID) -> dict[str, Any] | None:
        report = await get_workforce_integration().get_status(tenant_id)
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            lead = (await session.execute(select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id))).scalar_one_or_none()
            if lead is None:
                return None
            rows = (
                await session.execute(
                    select(Event)
                    .where(Event.tenant_id == tenant_id, Event.entity_type == "lead", Event.entity_id == lead_id, Event.event_type.in_(HALLA_EVENT_TYPES))
                    .order_by(Event.created_at)
                )
            ).scalars().all()
        state = derive_interaction_state(report.status.value, [(e.event_type, e.created_at) for e in rows])
        events = [
            {
                "type": e.event_type, "text": EVENT_TEXT.get(e.event_type, e.event_type), "at": e.created_at.isoformat(),
                "channel": (e.payload or {}).get("channel"), "summary": (e.payload or {}).get("summary"),
                "simulated": bool((e.payload or {}).get("simulated")),
            }
            for e in rows
        ]
        summaries = [e["summary"] for e in events if e["summary"]]
        hint: dict[str, Any] | None = None
        for key in await self._enabled_vertical_keys(tenant_id):
            fn = get_lead_context_provider(key)
            if fn is None:
                continue
            try:
                hint = (await fn(tenant_id, [lead_id])).get(lead_id, {}).get("next_action") or hint
            except Exception:  # noqa: BLE001
                continue
        return {
            "next_action": lead_next_action(lead.status, lead.qualification_status, lead_id, hint),
            "state": state, "label": INTERACTION_LABELS[state],
            "workforce": {"status": report.status.value, "mode": report.mode, "adapter_implemented": report.adapter_implemented, "message": report.message},
            "events": events,
            "summary": summaries[-1] if summaries else None,
        }

    async def interaction_counts(self, tenant_id: uuid.UUID) -> dict[str, int]:
        """How many leads have each recorded Halla event type — real events only."""
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            rows = (
                await session.execute(
                    select(Event.event_type, func.count()).where(Event.tenant_id == tenant_id, Event.event_type.in_(HALLA_EVENT_TYPES)).group_by(Event.event_type)
                )
            ).all()
        return {t: n for t, n in rows}

    # --------------------------------------------------------------- lead board

    async def lead_board(
        self, tenant_id: uuid.UUID, *, status: str | None = None, source: str | None = None, q: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> dict[str, Any]:
        limit = max(1, min(limit, 100))
        report = await get_workforce_integration().get_status(tenant_id)
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            where = [Lead.tenant_id == tenant_id]
            if status:
                where.append(Lead.status == status)
            if source:
                where.append(Lead.source == source)
            if q:
                like = f"%{q.strip()}%"
                where.append(or_(Lead.name.ilike(like), Lead.email.ilike(like), Lead.phone.ilike(like), Lead.service_requested.ilike(like)))
            total = int((await session.execute(select(func.count()).select_from(Lead).where(*where))).scalar_one())
            leads = (await session.execute(select(Lead).where(*where).order_by(Lead.created_at.desc()).limit(limit).offset(offset))).scalars().all()
            ids = [l.id for l in leads]
            ev_rows = (
                (
                    await session.execute(
                        select(Event.entity_id, Event.event_type, Event.created_at).where(
                            Event.tenant_id == tenant_id, Event.entity_type == "lead", Event.entity_id.in_(ids), Event.event_type.in_(HALLA_EVENT_TYPES)
                        )
                    )
                ).all()
                if ids
                else []
            )
            user_ids = {l.assigned_user_id for l in leads if l.assigned_user_id}
            users = (
                {u.id: u.full_name for u in (await session.execute(select(User).where(User.tenant_id == tenant_id, User.id.in_(user_ids)))).scalars()}
                if user_ids
                else {}
            )
        events_by_lead: dict[uuid.UUID, list[tuple[str, datetime]]] = {}
        for lid, etype, at in ev_rows:
            events_by_lead.setdefault(lid, []).append((etype, at))

        ctx: dict[uuid.UUID, dict[str, Any]] = {}
        for key in await self._enabled_vertical_keys(tenant_id):
            fn = get_lead_context_provider(key)
            if fn is None:
                continue
            try:
                ctx.update(await fn(tenant_id, ids))
            except Exception:  # noqa: BLE001
                continue

        rows = []
        for l in leads:
            c = ctx.get(l.id, {})
            state = derive_interaction_state(report.status.value, events_by_lead.get(l.id, []))
            rows.append(
                {
                    "id": str(l.id), "name": l.name, "status": l.status, "source": l.source, "created_at": l.created_at.isoformat(),
                    "priority": l.urgency, "assigned_to": users.get(l.assigned_user_id) if l.assigned_user_id else None,
                    "country": c.get("country") or l.location, "service": c.get("service") or l.service_requested,
                    "next_action": lead_next_action(l.status, l.qualification_status, l.id, c.get("next_action")),
                    "ai_state": state, "ai_label": INTERACTION_LABELS[state],
                }
            )
        return {"leads": rows, "total": total, "limit": limit, "offset": offset}

    async def _enabled_vertical_keys(self, tenant_id: uuid.UUID) -> list[str]:
        links = await self._verticals.list_enabled_for_organization(tenant_id)
        if not links:
            return []
        async with self._session_factory() as session:  # global reference table
            rows = (await session.execute(select(VerticalExtension.key).where(VerticalExtension.id.in_([l.vertical_extension_id for l in links])))).scalars().all()
        return list(rows)
