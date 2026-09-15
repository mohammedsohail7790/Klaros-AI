"""Manual invoice drafting — finance.create_invoice_draft and
finance.update_invoice_draft were fully implemented and registered but
had no route: the only ways to get an invoice into Klaros were a job
trigger or CSV import, so an owner could not write a one-off invoice by
hand. Also covers the due_date fix: CreateInvoiceDraftInput accepted a
due_date field that InvoiceService.create_manual_draft silently
discarded before this change.
"""

import uuid
from datetime import date, timedelta

import pytest

from app.models.actor import ActorType
from app.models.rbac import Role
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id, role=Role.OWNER):
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


async def test_create_manual_draft_respects_custom_due_date(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    customer = await tool_registry.execute("crm.create_customer", {"name": "Manual Draft Customer"}, _ctx(tenant_id))

    custom_due = date.today() + timedelta(days=10)
    created = await tool_registry.execute(
        "finance.create_invoice_draft",
        {
            "customer_id": customer.customer["id"],
            "line_items": [{"description": "Consulting", "quantity": "2", "unit_price": "75.00"}],
            "due_date": str(custom_due),
        },
        _ctx(tenant_id),
    )
    assert created.invoice["due_date"] == str(custom_due)
    assert created.invoice["status"] == "DRAFT"
    assert created.invoice["total"] == "150.00"


async def test_create_manual_draft_defaults_due_date_when_omitted(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    customer = await tool_registry.execute("crm.create_customer", {"name": "Default Due Customer"}, _ctx(tenant_id))

    created = await tool_registry.execute(
        "finance.create_invoice_draft",
        {"customer_id": customer.customer["id"], "line_items": [{"description": "Work", "quantity": "1", "unit_price": "50.00"}]},
        _ctx(tenant_id),
    )
    expected_default = date.today() + timedelta(days=30)
    assert created.invoice["due_date"] == str(expected_default)


async def test_update_draft_replaces_line_items_and_recalculates_total(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    customer = await tool_registry.execute("crm.create_customer", {"name": "Update Draft Customer"}, _ctx(tenant_id))
    created = await tool_registry.execute(
        "finance.create_invoice_draft",
        {"customer_id": customer.customer["id"], "line_items": [{"description": "Old", "quantity": "1", "unit_price": "10.00"}]},
        _ctx(tenant_id),
    )
    assert created.invoice["total"] == "10.00"

    updated = await tool_registry.execute(
        "finance.update_invoice_draft",
        {
            "invoice_id": created.invoice["id"],
            "line_items": [{"description": "New", "quantity": "3", "unit_price": "20.00"}],
        },
        _ctx(tenant_id),
    )
    assert updated.invoice["total"] == "60.00"


async def test_manual_invoice_draft_over_http(client) -> None:
    register = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "HTTP Manual Invoice Co", "full_name": "Owner Test",
            "email": "owner@httpmanualinvoiceco.com", "password": "supersecret1",
        },
    )
    assert register.status_code == 201
    token = register.json()["tokens"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    customer_resp = await client.post("/api/v1/customers", json={"name": "HTTP Manual Customer"}, headers=headers)
    assert customer_resp.status_code == 201, customer_resp.text
    customer_id = customer_resp.json()["customer"]["id"]

    create_resp = await client.post(
        "/api/v1/invoices",
        json={
            "customer_id": customer_id,
            "line_items": [{"description": "HTTP Work", "quantity": "1", "unit_price": "99.00"}],
        },
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    invoice_id = create_resp.json()["invoice"]["id"]
    assert create_resp.json()["invoice"]["status"] == "DRAFT"

    update_resp = await client.patch(
        f"/api/v1/invoices/{invoice_id}",
        json={"line_items": [{"description": "HTTP Work Updated", "quantity": "2", "unit_price": "50.00"}]},
        headers=headers,
    )
    assert update_resp.status_code == 200, update_resp.text
    assert update_resp.json()["invoice"]["total"] == "100.00"
