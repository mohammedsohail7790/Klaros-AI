"""section 25/28: the full CRM end-to-end scenario, actually run.

    new lead -> lead persisted -> lead.created -> qualification (via the
    event bus subscriber) -> lead.qualified -> availability -> appointment
    -> notification -> audit -> customer timeline
"""

import uuid

import pytest
from sqlalchemy import select

from app.models.actor import ActorType
from app.models.crm import Customer, Lead, LeadStatus, QualificationStatus
from app.models.event import Event, EventType
from app.models.notification import Notification
from app.models.rbac import Role
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id):
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=Role.OWNER)


async def test_lead_created_event_triggers_qualification_and_emits_lead_qualified(
    event_bus, tool_registry
) -> None:
    tenant_id = uuid.uuid4()
    context = _ctx(tenant_id)

    create_output = await tool_registry.execute(
        "crm.create_lead",
        {
            "name": "High Intent Lead",
            "source": "REFERRAL",
            "email": "hot-lead@example.com",
            "service_requested": "Roof replacement",
            "location": "Denver, CO",
            "urgency": "HIGH",
            "estimated_value": 8000,
        },
        context,
    )
    lead_id = create_output.lead["id"]

    async with event_bus.session_factory() as session:
        lead = await session.get(Lead, uuid.UUID(lead_id))
        assert lead.qualification_status == QualificationStatus.PENDING  # not qualified yet — async

    stats = await event_bus.process_pending(EventType.LEAD_CREATED)
    assert stats.succeeded >= 1

    async with event_bus.session_factory() as session:
        lead = await session.get(Lead, uuid.UUID(lead_id))
        assert lead.qualification_status == QualificationStatus.QUALIFIED
        assert lead.lead_score is not None
        assert lead.status == LeadStatus.QUALIFIED

        qualified_events = (
            await session.execute(
                select(Event).where(
                    Event.tenant_id == tenant_id, Event.event_type == EventType.LEAD_QUALIFIED
                )
            )
        ).scalars().all()
    assert len(qualified_events) == 1
    assert qualified_events[0].payload["qualification_status"] == "QUALIFIED"


async def test_full_crm_scenario_lead_to_timeline(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    context = _ctx(tenant_id)

    # 1. New lead arrives and is persisted.
    create_output = await tool_registry.execute(
        "crm.create_lead",
        {
            "name": "Full Scenario Lead",
            "source": "PHONE",
            "email": "scenario@example.com",
            "phone": "555-000-1111",
            "service_requested": "Water heater install",
            "location": "Seattle, WA",
            "urgency": "HIGH",
            "estimated_value": 3000,
        },
        context,
    )
    lead_id = create_output.lead["id"]

    async with event_bus.session_factory() as session:
        lead_created_events = (
            await session.execute(
                select(Event).where(
                    Event.tenant_id == tenant_id, Event.event_type == EventType.LEAD_CREATED
                )
            )
        ).scalars().all()
    assert len(lead_created_events) == 1

    # 2. Qualification workflow (event-bus triggered) runs.
    await event_bus.process_pending(EventType.LEAD_CREATED)

    async with event_bus.session_factory() as session:
        lead = await session.get(Lead, uuid.UUID(lead_id))
        assert lead.qualification_status in (QualificationStatus.QUALIFIED, QualificationStatus.REQUIRES_HUMAN)

    # 3. Customer is created directly (this lead had no existing match) and
    #    linked so the appointment/timeline steps have somewhere to attach to.
    customer_output = await tool_registry.execute(
        "crm.create_customer",
        {"name": "Full Scenario Lead", "email": "scenario@example.com", "phone": "555-000-1111"},
        context,
    )
    customer_id = customer_output.customer["id"]

    # 4. Check availability, then book an appointment for the qualified lead.
    availability = await tool_registry.execute(
        "crm.check_availability",
        {"date_from": "2026-09-15T09:00:00+00:00", "date_to": "2026-09-15T12:00:00+00:00", "duration_minutes": 60},
        context,
    )
    assert len(availability.slots) > 0
    slot = availability.slots[0]

    appointment_output = await tool_registry.execute(
        "crm.create_appointment",
        {
            "customer_id": customer_id,
            "lead_id": lead_id,
            "title": "Water heater install",
            "start_time": slot["start_time"],
            "end_time": slot["end_time"],
        },
        context,
    )
    appointment_id = appointment_output.appointment["id"]
    assert appointment_output.appointment["status"] == "TENTATIVE"

    # 5. appointment.created -> notification (event-bus handler).
    await event_bus.process_pending(EventType.APPOINTMENT_CREATED)
    async with event_bus.session_factory() as session:
        notifications = (
            await session.execute(select(Notification).where(Notification.tenant_id == tenant_id))
        ).scalars().all()
    assert any("appointment" in n.title.lower() for n in notifications)

    # 6. Audit trail exists for the tool calls that ran.
    from app.models.audit_log import AuditLog

    async with event_bus.session_factory() as session:
        audit_rows = (
            await session.execute(select(AuditLog).where(AuditLog.tenant_id == tenant_id))
        ).scalars().all()
    tool_names = {r.tool for r in audit_rows if r.tool}
    assert "crm.create_lead" in tool_names
    assert "crm.create_appointment" in tool_names

    # 7. Customer timeline reflects the appointment (lead isn't linked to this
    #    customer_id automatically — Phase 3 doesn't auto-convert on booking).
    timeline = await tool_registry.execute(
        "crm.get_customer_timeline", {"customer_id": customer_id}, context
    )
    assert any(e.type == "appointment" for e in timeline.entries)
