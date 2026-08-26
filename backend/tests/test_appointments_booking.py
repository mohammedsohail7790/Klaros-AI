import uuid

import pytest

from app.calendar.base import BookingRequest, DoubleBookingError
from app.calendar.internal_test_adapter import InternalTestCalendarAdapter
from app.models.actor import ActorType
from app.models.rbac import Role
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id):
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=Role.OWNER)


async def _make_customer(tool_registry, tenant_id) -> str:
    out = await tool_registry.execute("crm.create_customer", {"name": "Test Customer"}, _ctx(tenant_id))
    return out.customer["id"]


async def test_create_appointment_succeeds(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    customer_id = await _make_customer(tool_registry, tenant_id)

    out = await tool_registry.execute(
        "crm.create_appointment",
        {
            "customer_id": customer_id,
            "title": "AC repair",
            "start_time": "2026-09-01T14:00:00+00:00",
            "end_time": "2026-09-01T15:00:00+00:00",
        },
        _ctx(tenant_id),
    )
    assert out.appointment["status"] == "TENTATIVE"
    assert out.appointment["customer_id"] == customer_id


async def test_double_booking_same_technician_rejected(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    customer_id = await _make_customer(tool_registry, tenant_id)
    tech_id = str(uuid.uuid4())

    await tool_registry.execute(
        "crm.create_appointment",
        {
            "customer_id": customer_id,
            "title": "Job 1",
            "start_time": "2026-09-01T14:00:00+00:00",
            "end_time": "2026-09-01T15:00:00+00:00",
            "assigned_user_id": tech_id,
        },
        _ctx(tenant_id),
    )

    with pytest.raises(ValueError, match="conflicts"):
        await tool_registry.execute(
            "crm.create_appointment",
            {
                "customer_id": customer_id,
                "title": "Job 2 (overlaps)",
                "start_time": "2026-09-01T14:30:00+00:00",
                "end_time": "2026-09-01T15:30:00+00:00",
                "assigned_user_id": tech_id,
            },
            _ctx(tenant_id),
        )


async def test_non_overlapping_appointments_for_same_technician_succeed(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    customer_id = await _make_customer(tool_registry, tenant_id)
    tech_id = str(uuid.uuid4())

    await tool_registry.execute(
        "crm.create_appointment",
        {
            "customer_id": customer_id,
            "title": "Morning job",
            "start_time": "2026-09-01T09:00:00+00:00",
            "end_time": "2026-09-01T10:00:00+00:00",
            "assigned_user_id": tech_id,
        },
        _ctx(tenant_id),
    )
    second = await tool_registry.execute(
        "crm.create_appointment",
        {
            "customer_id": customer_id,
            "title": "Afternoon job",
            "start_time": "2026-09-01T10:00:00+00:00",
            "end_time": "2026-09-01T11:00:00+00:00",
            "assigned_user_id": tech_id,
        },
        _ctx(tenant_id),
    )
    assert second.appointment["status"] == "TENTATIVE"


async def test_appointment_idempotency_key_prevents_duplicate(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    customer_id = await _make_customer(tool_registry, tenant_id)
    payload = {
        "customer_id": customer_id,
        "title": "Repeat request",
        "start_time": "2026-09-02T09:00:00+00:00",
        "end_time": "2026-09-02T10:00:00+00:00",
        "idempotency_key": "sms-booking-req-1",
    }

    first = await tool_registry.execute("crm.create_appointment", payload, _ctx(tenant_id))
    second = await tool_registry.execute("crm.create_appointment", payload, _ctx(tenant_id))

    assert first.appointment["id"] == second.appointment["id"]


async def test_reschedule_into_conflicting_slot_rejected(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    customer_id = await _make_customer(tool_registry, tenant_id)
    tech_id = str(uuid.uuid4())

    await tool_registry.execute(
        "crm.create_appointment",
        {
            "customer_id": customer_id,
            "title": "Fixed job",
            "start_time": "2026-09-03T09:00:00+00:00",
            "end_time": "2026-09-03T10:00:00+00:00",
            "assigned_user_id": tech_id,
        },
        _ctx(tenant_id),
    )
    movable = await tool_registry.execute(
        "crm.create_appointment",
        {
            "customer_id": customer_id,
            "title": "Movable job",
            "start_time": "2026-09-03T11:00:00+00:00",
            "end_time": "2026-09-03T12:00:00+00:00",
            "assigned_user_id": tech_id,
        },
        _ctx(tenant_id),
    )

    with pytest.raises(ValueError, match="conflicts"):
        await tool_registry.execute(
            "crm.reschedule_appointment",
            {
                "appointment_id": movable.appointment["id"],
                "start_time": "2026-09-03T09:30:00+00:00",
                "end_time": "2026-09-03T10:30:00+00:00",
            },
            _ctx(tenant_id),
        )


async def test_cancel_appointment_frees_the_slot(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    customer_id = await _make_customer(tool_registry, tenant_id)
    tech_id = str(uuid.uuid4())

    created = await tool_registry.execute(
        "crm.create_appointment",
        {
            "customer_id": customer_id,
            "title": "To be cancelled",
            "start_time": "2026-09-04T09:00:00+00:00",
            "end_time": "2026-09-04T10:00:00+00:00",
            "assigned_user_id": tech_id,
        },
        _ctx(tenant_id),
    )
    cancelled = await tool_registry.execute(
        "crm.cancel_appointment", {"appointment_id": created.appointment["id"]}, _ctx(tenant_id)
    )
    assert cancelled.appointment["status"] == "CANCELLED"

    # Same slot, same technician, should now succeed since the conflicting
    # appointment is cancelled (not counted as an ACTIVE_STATUSES conflict).
    rebooked = await tool_registry.execute(
        "crm.create_appointment",
        {
            "customer_id": customer_id,
            "title": "Rebooked",
            "start_time": "2026-09-04T09:00:00+00:00",
            "end_time": "2026-09-04T10:00:00+00:00",
            "assigned_user_id": tech_id,
        },
        _ctx(tenant_id),
    )
    assert rebooked.appointment["status"] == "TENTATIVE"


async def test_create_appointment_rejects_customer_from_another_tenant(tool_registry) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    customer_b = await _make_customer(tool_registry, tenant_b)

    with pytest.raises(ValueError, match="not found"):
        await tool_registry.execute(
            "crm.create_appointment",
            {
                "customer_id": customer_b,
                "title": "Cross-tenant attempt",
                "start_time": "2026-09-05T09:00:00+00:00",
                "end_time": "2026-09-05T10:00:00+00:00",
            },
            _ctx(tenant_a),
        )


async def test_internal_test_calendar_get_availability_excludes_booked_slots(event_bus) -> None:
    from datetime import datetime, timezone

    tenant_id = uuid.uuid4()
    calendar = InternalTestCalendarAdapter(event_bus.session_factory)
    async with event_bus.session_factory() as session:
        from app.models.crm import Customer

        customer = Customer(tenant_id=tenant_id, name="Avail Test")
        session.add(customer)
        await session.commit()
        await session.refresh(customer)

    date_from = datetime(2026, 9, 10, 9, 0, tzinfo=timezone.utc)
    date_to = datetime(2026, 9, 10, 11, 0, tzinfo=timezone.utc)

    before = await calendar.get_availability(tenant_id, date_from=date_from, date_to=date_to, duration_minutes=60)
    assert len(before) >= 1

    await calendar.create_event(
        BookingRequest(
            tenant_id=tenant_id,
            customer_id=customer.id,
            title="Booked",
            start_time=date_from,
            end_time=date_from.replace(hour=10),
        )
    )

    after = await calendar.get_availability(tenant_id, date_from=date_from, date_to=date_to, duration_minutes=60)
    assert all(s.start_time != date_from for s in after)
