"""finance.bulk_import_invoices — lets a new tenant bring their currently-
owed AR into Klaros as real invoices (never replayed through the normal
draft->approval lifecycle), matching or creating customers by name/email,
and correctly landing in SENT/PARTIALLY_PAID/PAID based on amount_paid."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.actor import ActorType
from app.models.crm import Customer
from app.models.finance import Invoice, InvoiceStatus
from app.models.rbac import Role
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id, role=Role.OWNER):
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


def _row(**overrides):
    row = {
        "customer_name": "Imported Customer",
        "issue_date": "2026-08-01",
        "due_date": "2026-09-01",
        "amount": "500.00",
    }
    row.update(overrides)
    return row


async def test_unpaid_invoice_imports_as_sent(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    out = await tool_registry.execute(
        "finance.bulk_import_invoices", {"invoices": [_row(customer_email="unpaid@example.com")]}, _ctx(tenant_id)
    )
    assert out.created_count == 1
    assert out.customers_created_count == 1

    async with event_bus.session_factory() as session:
        invoice = await session.get(Invoice, uuid.UUID(out.results[0].invoice_id))
    assert invoice.status == InvoiceStatus.SENT
    assert invoice.total == Decimal("500.00")
    assert invoice.amount_due == Decimal("500.00")
    assert invoice.amount_paid == Decimal("0")


async def test_fully_paid_invoice_imports_as_paid(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    out = await tool_registry.execute(
        "finance.bulk_import_invoices",
        {"invoices": [_row(customer_email="paid@example.com", amount="300.00", amount_paid="300.00")]},
        _ctx(tenant_id),
    )
    async with event_bus.session_factory() as session:
        invoice = await session.get(Invoice, uuid.UUID(out.results[0].invoice_id))
    assert invoice.status == InvoiceStatus.PAID
    assert invoice.amount_due == Decimal("0")
    assert invoice.paid_at is not None


async def test_partially_paid_invoice_imports_correctly(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    out = await tool_registry.execute(
        "finance.bulk_import_invoices",
        {"invoices": [_row(customer_email="partial@example.com", amount="400.00", amount_paid="100.00")]},
        _ctx(tenant_id),
    )
    async with event_bus.session_factory() as session:
        invoice = await session.get(Invoice, uuid.UUID(out.results[0].invoice_id))
    assert invoice.status == InvoiceStatus.PARTIALLY_PAID
    assert invoice.amount_due == Decimal("300.00")


async def test_matches_existing_customer_instead_of_duplicating(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    async with event_bus.session_factory() as session:
        customer = Customer(tenant_id=tenant_id, name="Existing Ivan", email="ivan@example.com")
        session.add(customer)
        await session.commit()
        await session.refresh(customer)

    out = await tool_registry.execute(
        "finance.bulk_import_invoices",
        {"invoices": [_row(customer_name="Ivan Anything", customer_email="IVAN@example.com")]},
        _ctx(tenant_id),
    )
    assert out.created_count == 1
    assert out.customers_created_count == 0

    async with event_bus.session_factory() as session:
        invoice = await session.get(Invoice, uuid.UUID(out.results[0].invoice_id))
    assert invoice.customer_id == customer.id


async def test_matches_existing_customer_by_exact_name_when_no_email(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    async with event_bus.session_factory() as session:
        customer = Customer(tenant_id=tenant_id, name="Name Only Nora")
        session.add(customer)
        await session.commit()
        await session.refresh(customer)

    out = await tool_registry.execute(
        "finance.bulk_import_invoices", {"invoices": [_row(customer_name="name only nora")]}, _ctx(tenant_id)
    )
    assert out.customers_created_count == 0
    async with event_bus.session_factory() as session:
        invoice = await session.get(Invoice, uuid.UUID(out.results[0].invoice_id))
    assert invoice.customer_id == customer.id


async def test_duplicate_invoice_number_is_skipped_not_fatal(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    out = await tool_registry.execute(
        "finance.bulk_import_invoices",
        {
            "invoices": [
                _row(customer_email="a@example.com", invoice_number="EXT-001"),
                _row(customer_email="b@example.com", invoice_number="EXT-001"),
            ]
        },
        _ctx(tenant_id),
    )
    assert out.created_count == 1
    assert out.skipped_count == 1
    assert out.results[1].status == "skipped"
    assert "EXT-001" in out.results[1].reason


async def test_appears_in_ar_aging(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    await tool_registry.execute(
        "finance.bulk_import_invoices",
        {"invoices": [_row(customer_email="aging@example.com", amount="250.00")]},
        _ctx(tenant_id),
    )
    aging = await tool_registry.execute("finance.get_ar_aging", {}, _ctx(tenant_id))
    assert Decimal(aging.total) == Decimal("250.00")


async def test_bulk_import_invoices_over_http_requires_auth(client) -> None:
    resp = await client.post(
        "/api/v1/invoices/import",
        json=[{"customer_name": "HTTP Test", "issue_date": "2026-08-01", "due_date": "2026-09-01", "amount": "100.00"}],
    )
    assert resp.status_code == 401
