import uuid

import pytest

from app.models.actor import ActorType
from app.models.crm import Customer, CustomerStatus, Lead
from app.models.rbac import Role
from app.services.customer_matching import find_matching_customer, normalize_phone
from app.services.lead_service import CreateLeadInput, LeadService
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id, role=Role.OWNER, actor_type=ActorType.USER):
    return ExecutionContext(tenant_id=tenant_id, actor_type=actor_type, actor_id=uuid.uuid4(), role=role)


async def test_phone_normalization_matches_equivalent_formats() -> None:
    assert normalize_phone("(555) 123-4567") == normalize_phone("+1 555-123-4567")
    assert normalize_phone("5551234567") == "5551234567"
    assert normalize_phone(None) is None
    assert normalize_phone("") is None


async def test_lead_creation_matches_existing_customer_by_email(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    async with event_bus.session_factory() as session:
        customer = Customer(tenant_id=tenant_id, name="Jane Existing", email="jane@example.com")
        session.add(customer)
        await session.commit()
        await session.refresh(customer)

    lead_service = LeadService(event_bus.session_factory, event_bus)
    lead, deduped = await lead_service.create_lead(
        tenant_id,
        CreateLeadInput(name="Jane New Inquiry", source="WEB", email="JANE@example.com"),
    )

    assert deduped is False
    assert lead.customer_id == customer.id


async def test_lead_creation_idempotency_key_deduplicates(event_bus) -> None:
    tenant_id = uuid.uuid4()
    lead_service = LeadService(event_bus.session_factory, event_bus)
    data = CreateLeadInput(name="Bob", source="PHONE", idempotency_key="webhook-delivery-1")

    first, first_dup = await lead_service.create_lead(tenant_id, data)
    second, second_dup = await lead_service.create_lead(tenant_id, data)

    assert first.id == second.id
    assert first_dup is False
    assert second_dup is True

    async with event_bus.session_factory() as session:
        from sqlalchemy import func, select

        count = (
            await session.execute(
                select(func.count(Lead.id)).where(Lead.tenant_id == tenant_id)
            )
        ).scalar_one()
    assert count == 1


async def test_no_customer_match_without_email_or_phone(event_bus) -> None:
    tenant_id = uuid.uuid4()
    async with event_bus.session_factory() as session:
        match = await find_matching_customer(session, tenant_id=tenant_id, email=None, phone=None)
    assert match is None


async def test_qualify_lead_tool_scores_and_persists(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    context = _ctx(tenant_id)

    create_output = await tool_registry.execute(
        "crm.create_lead",
        {
            "name": "Urgent Job",
            "source": "REFERRAL",
            "service_requested": "AC repair",
            "location": "Austin, TX",
            "urgency": "EMERGENCY",
            "estimated_value": 6000,
        },
        context,
    )
    lead_id = create_output.lead["id"]

    qualify_output = await tool_registry.execute("crm.qualify_lead", {"lead_id": lead_id}, context)

    assert qualify_output.score >= 70
    assert qualify_output.qualification_status == "QUALIFIED"
    assert "Score" in qualify_output.reason


async def test_search_leads_filters_and_paginates(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    context = _ctx(tenant_id)

    for i in range(3):
        await tool_registry.execute(
            "crm.create_lead", {"name": f"Lead {i}", "source": "WEB"}, context
        )

    result = await tool_registry.execute(
        "crm.search_leads", {"limit": 2, "offset": 0}, context
    )
    assert result.total == 3
    assert len(result.leads) == 2


async def test_create_customer_and_add_note(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    context = _ctx(tenant_id)

    customer_output = await tool_registry.execute(
        "crm.create_customer", {"name": "Acme Corp", "email": "ops@acme.com"}, context
    )
    customer_id = customer_output.customer["id"]

    note_output = await tool_registry.execute(
        "crm.create_note", {"customer_id": customer_id, "body": "Called, left voicemail."}, context
    )
    assert note_output.note_id

    timeline = await tool_registry.execute(
        "crm.get_customer_timeline", {"customer_id": customer_id}, context
    )
    assert any(e.type == "note" for e in timeline.entries)


async def test_customer_summary_says_so_when_no_data(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    context = _ctx(tenant_id)
    customer_output = await tool_registry.execute(
        "crm.create_customer", {"name": "New Prospect"}, context
    )
    customer_id = customer_output.customer["id"]

    summary = await tool_registry.execute(
        "crm.generate_customer_summary", {"customer_id": customer_id}, context
    )
    assert "insufficient data" in summary.summary.lower()
