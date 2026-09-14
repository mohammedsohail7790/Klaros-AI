"""crm.bulk_import_leads — lets a new tenant bring an existing prospect
pipeline into Klaros. Reuses LeadService.create_lead's own customer-
matching logic per row; source is always LeadSource.OTHER."""

import uuid

import pytest
from sqlalchemy import select

from app.models.actor import ActorType
from app.models.crm import Customer, Lead, LeadSource
from app.models.rbac import Role
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id, role=Role.OWNER):
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


async def test_bulk_import_creates_every_lead_with_source_other(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    out = await tool_registry.execute(
        "crm.bulk_import_leads",
        {
            "leads": [
                {"name": "Prospect One", "email": "prospect1@example.com"},
                {"name": "Prospect Two", "phone": "555-100-0002"},
            ]
        },
        _ctx(tenant_id),
    )
    assert out.created_count == 2
    assert len(out.lead_ids) == 2

    async with event_bus.session_factory() as session:
        leads = (await session.execute(select(Lead).where(Lead.tenant_id == tenant_id))).scalars().all()
    assert {l.source for l in leads} == {LeadSource.OTHER}


async def test_bulk_import_matches_existing_customers_by_email(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    async with event_bus.session_factory() as session:
        customer = Customer(tenant_id=tenant_id, name="Existing Hank", email="hank@example.com")
        session.add(customer)
        await session.commit()
        await session.refresh(customer)

    out = await tool_registry.execute(
        "crm.bulk_import_leads",
        {"leads": [{"name": "Hank Inquiry", "email": "HANK@example.com"}, {"name": "Fresh Prospect"}]},
        _ctx(tenant_id),
    )
    assert out.created_count == 2
    assert out.matched_existing_customer_count == 1


async def test_bulk_import_leads_over_http_requires_auth(client) -> None:
    resp = await client.post("/api/v1/leads/import", json=[{"name": "HTTP Lead"}])
    assert resp.status_code == 401
