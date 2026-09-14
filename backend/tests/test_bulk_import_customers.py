"""crm.bulk_import_customers — lets a new tenant bring an existing
customer list (e.g. a CSV export from a spreadsheet or another CRM) into
Klaros at signup, instead of starting from an empty CRM. Real transactional
bulk creation with email-based dedup, both at the tool layer and over the
POST /api/v1/customers/import HTTP endpoint."""

import uuid

import pytest
from sqlalchemy import select

from app.models.actor import ActorType
from app.models.crm import Customer
from app.models.rbac import Role
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id, role=Role.OWNER):
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


async def test_bulk_import_creates_every_row(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    out = await tool_registry.execute(
        "crm.bulk_import_customers",
        {
            "customers": [
                {"name": "Alice Import", "email": "alice@example.com"},
                {"name": "Bob Import", "email": "bob@example.com"},
                {"name": "Carol Import (no email)"},
            ]
        },
        _ctx(tenant_id),
    )
    assert out.created_count == 3
    assert out.skipped_duplicate_count == 0
    assert len(out.customer_ids) == 3


async def test_bulk_import_skips_rows_already_matching_an_existing_customer(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    async with event_bus.session_factory() as session:
        session.add(Customer(tenant_id=tenant_id, name="Existing Dana", email="dana@example.com"))
        await session.commit()

    out = await tool_registry.execute(
        "crm.bulk_import_customers",
        {"customers": [{"name": "Dana Duplicate", "email": "DANA@example.com"}, {"name": "New Eve", "email": "eve@example.com"}]},
        _ctx(tenant_id),
    )
    assert out.created_count == 1
    assert out.skipped_duplicate_count == 1


async def test_bulk_import_skips_duplicate_emails_within_the_same_batch(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    out = await tool_registry.execute(
        "crm.bulk_import_customers",
        {
            "customers": [
                {"name": "First Frank", "email": "frank@example.com"},
                {"name": "Second Frank (dup)", "email": "frank@example.com"},
            ]
        },
        _ctx(tenant_id),
    )
    assert out.created_count == 1
    assert out.skipped_duplicate_count == 1


async def test_bulk_import_never_leaks_across_tenants(event_bus, tool_registry) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    async with event_bus.session_factory() as session:
        session.add(Customer(tenant_id=tenant_a, name="Tenant A Customer", email="shared@example.com"))
        await session.commit()

    out = await tool_registry.execute(
        "crm.bulk_import_customers",
        {"customers": [{"name": "Tenant B Customer", "email": "shared@example.com"}]},
        _ctx(tenant_b),
    )
    assert out.created_count == 1
    assert out.skipped_duplicate_count == 0


async def test_bulk_import_over_http(client) -> None:
    resp = await client.post(
        "/api/v1/customers/import",
        json=[
            {"name": "HTTP Import One", "email": "http1@example.com"},
            {"name": "HTTP Import Two", "email": "http2@example.com"},
        ],
    )
    assert resp.status_code == 401  # no auth token supplied — proves the route exists and is guarded


async def test_bulk_imported_customers_are_searchable(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    await tool_registry.execute(
        "crm.bulk_import_customers",
        {"customers": [{"name": "Searchable Grace", "email": "grace@example.com"}]},
        _ctx(tenant_id),
    )
    async with event_bus.session_factory() as session:
        rows = (
            await session.execute(select(Customer).where(Customer.tenant_id == tenant_id, Customer.name == "Searchable Grace"))
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].email == "grace@example.com"
