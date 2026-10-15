"""Turns Medical Tourism enquiries into work a person can see, whichever channel they came through.

Until now a coordinator task existed only when a phone call (Halla) handed a case over, so a website enquiry produced no task at all and Operations stayed empty. Now:
  an enquiry for a Medical Tourism business   -> an OPEN "new_enquiry" task: review it and match a clinic
  that enquiry is qualified                  -> an OPEN "provider_options" task: line up clinic options (the same task a phone hand-over creates, never doubled)
  a referral for the enquiry is sent         -> those tasks are closed automatically (see services/mt_referrals.py)

One task per enquiry and kind (a redelivered event never doubles it). A business without the module gets nothing, and nothing from the patient is copied into a task.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.events.bus import EventBus
from app.models.event import Event, EventType
from app.services import mt_handoff
from app.services.vertical_extension_service import VerticalExtensionService

logger = structlog.get_logger(__name__)


def register_mt_task_handlers(bus: EventBus, session_factory: async_sessionmaker) -> None:
    verticals = VerticalExtensionService(session_factory)

    async def _is_medical_tourism(tenant_id: uuid.UUID) -> bool:
        try:
            return await verticals.is_enabled_for_organization(tenant_id, "medical_tourism")
        except Exception:  # noqa: BLE001 - an unknown vertical or a lookup problem means "not a Medical Tourism business"
            return False

    async def _lead_id(event: Event) -> uuid.UUID | None:
        raw = event.entity_id or (event.payload or {}).get("lead_id")
        try:
            return uuid.UUID(str(raw)) if raw else None
        except ValueError:
            return None

    async def new_enquiry(event: Event) -> None:
        lead_id = await _lead_id(event)
        if lead_id is None:
            return
        async with session_factory() as session:
            await set_tenant_context(session, event.tenant_id)
            if not await _is_medical_tourism(event.tenant_id):
                return
            await mt_handoff.ensure_task(session, event.tenant_id, lead_id, kind="new_enquiry", reason_code="new_enquiry", dedupe_key=str(lead_id))
            await session.commit()

    async def qualified(event: Event) -> None:
        lead_id = await _lead_id(event)
        if lead_id is None:
            return
        async with session_factory() as session:
            await set_tenant_context(session, event.tenant_id)
            if not await _is_medical_tourism(event.tenant_id):
                return
            await mt_handoff.ensure_task(session, event.tenant_id, lead_id, kind="provider_options", reason_code=None, dedupe_key=str(lead_id))
            await session.commit()

    bus.subscribe(EventType.LEAD_CREATED, "mt_new_enquiry_task_handler", new_enquiry)
    bus.subscribe(EventType.LEAD_QUALIFIED, "mt_qualified_enquiry_task_handler", qualified)
