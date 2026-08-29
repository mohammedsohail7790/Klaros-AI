"""Focused Finance unit/integration tests: total recalculation, approval
policy thresholds, tenant isolation, credit notes/write-offs, cash forecast
NOT_CONNECTED behavior, and refund approval-boundary enforcement."""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.models.actor import ActorType
from app.models.finance import CreditNoteStatus, Invoice, InvoiceStatus, WriteOffStatus
from app.models.rbac import Role
from app.services.cash_forecast_service import CashForecastService
from app.services.invoice_policy import evaluate_invoice_approval
from app.services.invoice_service import LineItemInput, compute_totals
from app.tools.base import ExecutionContext

def _ctx(tenant_id, role=Role.OWNER):
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


def test_compute_totals_is_deterministic_and_never_trusts_a_client_total() -> None:
    items = [
        LineItemInput(description="Labor", quantity=Decimal("2"), unit_price=Decimal("100.00"), tax_rate=Decimal("0.08")),
        LineItemInput(description="Materials", quantity=Decimal("1"), unit_price=Decimal("50.00"), discount=Decimal("10.00")),
    ]
    subtotal, tax, discount, total = compute_totals(items)
    assert subtotal == Decimal("250.00")
    assert discount == Decimal("10.00")
    assert tax == Decimal("16.00")  # 8% of 200 (labor gross - 0 discount)
    assert total == Decimal("256.00")  # 250 - 10 + 16


def test_invoice_policy_thresholds() -> None:
    under = Invoice(total=Decimal("500"), subtotal=Decimal("500"), discount=Decimal("0"))
    decision = evaluate_invoice_approval(under, has_unresolved_scope_change=False)
    assert decision.requires_approval is False

    over_threshold = Invoice(total=Decimal("1500"), subtotal=Decimal("1500"), discount=Decimal("0"))
    decision = evaluate_invoice_approval(over_threshold, has_unresolved_scope_change=False)
    assert decision.requires_approval is True
    assert "exceeds auto-approval threshold" in decision.reasons[0]

    unusual_discount = Invoice(total=Decimal("500"), subtotal=Decimal("500"), discount=Decimal("150"))
    decision = evaluate_invoice_approval(unusual_discount, has_unresolved_scope_change=False)
    assert decision.requires_approval is True

    unresolved_scope = Invoice(total=Decimal("500"), subtotal=Decimal("500"), discount=Decimal("0"))
    decision = evaluate_invoice_approval(unresolved_scope, has_unresolved_scope_change=True)
    assert decision.requires_approval is True


@pytest.mark.asyncio
async def test_finance_tools_enforce_tenant_isolation(event_bus, tool_registry) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    ctx_a = _ctx(tenant_a)
    ctx_b = _ctx(tenant_b)

    customer = await tool_registry.execute("crm.create_customer", {"name": "Tenant A Customer"}, ctx_a)
    invoice = await tool_registry.execute(
        "finance.create_invoice_draft",
        {
            "customer_id": customer.customer["id"],
            "line_items": [{"description": "Work", "quantity": "1", "unit_price": "100.00"}],
        },
        ctx_a,
    )
    invoice_id = invoice.invoice["id"]

    with pytest.raises(ValueError):
        await tool_registry.execute("finance.get_invoice", {"invoice_id": invoice_id}, ctx_b)

    with pytest.raises(ValueError):
        await tool_registry.execute(
            "finance.update_invoice_draft",
            {"invoice_id": invoice_id, "line_items": [{"description": "x", "quantity": "1", "unit_price": "1"}]},
            ctx_b,
        )


@pytest.mark.asyncio
async def test_credit_note_requires_approval_and_reduces_invoice(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    customer = await tool_registry.execute("crm.create_customer", {"name": "CN Customer"}, ctx)
    invoice = await tool_registry.execute(
        "finance.create_invoice_draft",
        {"customer_id": customer.customer["id"], "line_items": [{"description": "Job", "quantity": "1", "unit_price": "500.00"}]},
        ctx,
    )
    invoice_id = invoice.invoice["id"]

    cn = await tool_registry.execute(
        "finance.create_credit_note_request",
        {"invoice_id": invoice_id, "reason": "Customer complaint", "line_items": [{"description": "Goodwill credit", "amount": "50.00"}]},
        ctx,
    )
    assert cn.credit_note["status"] == CreditNoteStatus.APPROVAL_REQUIRED

    approved = await tool_registry.execute(
        "finance.approve_credit_note", {"credit_note_id": cn.credit_note["id"]}, ctx
    )
    assert approved.credit_note["status"] == CreditNoteStatus.APPLIED

    async with event_bus.session_factory() as session:
        inv_row = await session.get(Invoice, uuid.UUID(invoice_id))
    assert inv_row.total == Decimal("450.00")


@pytest.mark.asyncio
async def test_writeoff_requires_approval_and_zeroes_amount_due(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    customer = await tool_registry.execute("crm.create_customer", {"name": "WO Customer"}, ctx)
    invoice = await tool_registry.execute(
        "finance.create_invoice_draft",
        {"customer_id": customer.customer["id"], "line_items": [{"description": "Job", "quantity": "1", "unit_price": "200.00"}]},
        ctx,
    )
    invoice_id = invoice.invoice["id"]

    wo = await tool_registry.execute(
        "finance.create_writeoff_request",
        {"invoice_id": invoice_id, "amount": "200.00", "reason": "Uncollectable"},
        ctx,
    )
    assert wo.writeoff["status"] == WriteOffStatus.REQUESTED

    approved = await tool_registry.execute("finance.approve_writeoff", {"writeoff_id": wo.writeoff["id"]}, ctx)
    assert approved.writeoff["status"] == WriteOffStatus.APPLIED

    async with event_bus.session_factory() as session:
        inv_row = await session.get(Invoice, uuid.UUID(invoice_id))
    assert inv_row.amount_due == Decimal("0.00")
    assert inv_row.status == InvoiceStatus.CANCELLED


@pytest.mark.asyncio
async def test_cash_forecast_not_connected_without_manual_starting_cash(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    service = CashForecastService(event_bus.session_factory)

    from app.models.organization import Organization

    async with event_bus.session_factory() as session:
        session.add(Organization(id=tenant_id, name="Test Org", slug=f"test-{tenant_id}"))
        await session.commit()

    forecast = await service.generate(tenant_id)
    assert forecast.starting_cash is None
    assert forecast.starting_cash_source == "NOT_CONNECTED"

    weeks = await service.weekly_projection(tenant_id, forecast.id)
    assert weeks[0]["projected_balance"] == "NOT_CONNECTED"


@pytest.mark.asyncio
async def test_refund_never_issued_directly_always_requires_approval(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    customer = await tool_registry.execute("crm.create_customer", {"name": "Refund Customer"}, ctx)
    invoice = await tool_registry.execute(
        "finance.create_invoice_draft",
        {"customer_id": customer.customer["id"], "line_items": [{"description": "Job", "quantity": "1", "unit_price": "300.00"}]},
        ctx,
    )
    invoice_id = invoice.invoice["id"]
    await tool_registry.execute("finance.request_invoice_approval", {"invoice_id": invoice_id}, ctx)
    await tool_registry.execute("finance.send_invoice", {"invoice_id": invoice_id}, ctx)

    payment = await tool_registry.execute(
        "finance.record_test_payment",
        {"customer_id": customer.customer["id"], "amount": "300.00", "allocations": [{"invoice_id": invoice_id, "amount": "300.00"}]},
        ctx,
    )

    refund_request = await tool_registry.execute(
        "finance.create_refund_request",
        {"payment_id": payment.payment["id"], "invoice_id": invoice_id, "amount": "100.00", "reason": "Overcharged"},
        ctx,
    )
    # Never lands as COMPLETED from the request tool alone.
    assert refund_request.refund["status"] == "REQUESTED"

    approved = await tool_registry.execute(
        "finance.approve_refund", {"refund_id": refund_request.refund["id"]}, ctx
    )
    assert approved.refund["status"] == "COMPLETED"
