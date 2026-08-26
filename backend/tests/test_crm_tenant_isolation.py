import uuid

import pytest

from app.models.actor import ActorType
from app.models.rbac import Role
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id):
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=Role.OWNER)


async def test_tenant_a_cannot_read_tenant_b_lead(tool_registry) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    created = await tool_registry.execute(
        "crm.create_lead", {"name": "A's Lead", "source": "WEB"}, _ctx(tenant_a)
    )
    lead_id = created.lead["id"]

    with pytest.raises(ValueError, match="not found"):
        await tool_registry.execute("crm.get_lead", {"lead_id": lead_id}, _ctx(tenant_b))

    # Owner of the correct tenant can read it.
    ok = await tool_registry.execute("crm.get_lead", {"lead_id": lead_id}, _ctx(tenant_a))
    assert ok.lead["id"] == lead_id


async def test_tenant_a_cannot_read_tenant_b_customer(tool_registry) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    created = await tool_registry.execute(
        "crm.create_customer", {"name": "A's Customer"}, _ctx(tenant_a)
    )
    customer_id = created.customer["id"]

    with pytest.raises(ValueError, match="not found"):
        await tool_registry.execute("crm.get_customer", {"customer_id": customer_id}, _ctx(tenant_b))


async def test_tenant_a_search_never_returns_tenant_b_leads(tool_registry) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await tool_registry.execute("crm.create_lead", {"name": "A1", "source": "WEB"}, _ctx(tenant_a))
    await tool_registry.execute("crm.create_lead", {"name": "B1", "source": "WEB"}, _ctx(tenant_b))
    await tool_registry.execute("crm.create_lead", {"name": "B2", "source": "WEB"}, _ctx(tenant_b))

    result_a = await tool_registry.execute("crm.search_leads", {}, _ctx(tenant_a))
    result_b = await tool_registry.execute("crm.search_leads", {}, _ctx(tenant_b))

    assert result_a.total == 1
    assert result_b.total == 2
    assert all(lead["name"] != "B1" and lead["name"] != "B2" for lead in result_a.leads)


async def test_tenant_a_cannot_cancel_tenant_b_appointment(tool_registry) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    customer = await tool_registry.execute("crm.create_customer", {"name": "B Customer"}, _ctx(tenant_b))
    customer_id = customer.customer["id"]

    appt = await tool_registry.execute(
        "crm.create_appointment",
        {
            "customer_id": customer_id,
            "title": "B's appointment",
            "start_time": "2026-09-01T10:00:00+00:00",
            "end_time": "2026-09-01T11:00:00+00:00",
        },
        _ctx(tenant_b),
    )
    appointment_id = appt.appointment["id"]

    with pytest.raises(ValueError, match="not found"):
        await tool_registry.execute(
            "crm.cancel_appointment", {"appointment_id": appointment_id}, _ctx(tenant_a)
        )
