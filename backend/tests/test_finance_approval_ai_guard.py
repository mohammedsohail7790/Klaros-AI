"""Phase 12F: a real, found-and-fixed P0 vulnerability — finance.approve_refund/
reject_refund, approve_invoice/reject_invoice, approve_credit_note/
reject_credit_note, and approve_writeoff/reject_writeoff all relied SOLELY
on role-based permission gating (no explicit actor-type check), unlike the
generic approval.approve_action/reject_action tools, which have always had
one. Role.MANAGER — the only role AIExecutionService has ever been invoked
with in this codebase (see app/services/morning_brief_service.py) — DOES
hold every one of APPROVE_REFUND/APPROVE_INVOICE/APPROVE_CREDIT_NOTE/
APPROVE_WRITEOFF. Before this phase, an AI-originated tool call with that
role could reach and complete any of these with no human ever involved,
proven directly against the real ToolRegistry before the fix — a captured
proof-of-concept, not a hypothetical. Every tool below now has the same
explicit ActorType.AI guard the generic approval tools already had."""

import uuid

import pytest

from app.models.actor import ActorType
from app.models.rbac import Role, role_has_permission, Permission
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ai_ctx(tenant_id: uuid.UUID, role: Role = Role.MANAGER) -> ExecutionContext:
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.AI, actor_id=None, role=role)


def _human_ctx(tenant_id: uuid.UUID, role: Role = Role.OWNER) -> ExecutionContext:
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


async def test_manager_role_actually_holds_every_finance_approval_permission() -> None:
    """Documents exactly why role-only gating was insufficient — this is
    not a hypothetical, Role.MANAGER genuinely holds all four."""
    assert role_has_permission(Role.MANAGER, Permission.APPROVE_REFUND) is True
    assert role_has_permission(Role.MANAGER, Permission.APPROVE_INVOICE) is True
    assert role_has_permission(Role.MANAGER, Permission.APPROVE_CREDIT_NOTE) is True
    assert role_has_permission(Role.MANAGER, Permission.APPROVE_WRITEOFF) is True


async def _create_paid_invoice_and_refund_request(tool_registry, tenant_id: uuid.UUID) -> str:
    ctx = _human_ctx(tenant_id)
    customer = await tool_registry.execute("crm.create_customer", {"name": "Guard Test Customer"}, ctx)
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
        {"payment_id": payment.payment["id"], "invoice_id": invoice_id, "amount": "100.00", "reason": "test"},
        ctx,
    )
    return refund_request.refund["id"], invoice_id


async def test_ai_cannot_approve_a_refund(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    refund_id, _ = await _create_paid_invoice_and_refund_request(tool_registry, tenant_id)

    with pytest.raises(ValueError, match="AI cannot approve a refund"):
        await tool_registry.execute("finance.approve_refund", {"refund_id": refund_id}, _ai_ctx(tenant_id))


async def test_ai_cannot_reject_a_refund(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    refund_id, _ = await _create_paid_invoice_and_refund_request(tool_registry, tenant_id)

    with pytest.raises(ValueError, match="AI cannot reject a refund"):
        await tool_registry.execute("finance.reject_refund", {"refund_id": refund_id}, _ai_ctx(tenant_id))


async def test_human_can_still_approve_a_refund(event_bus, tool_registry) -> None:
    """The fix must not break the legitimate human path."""
    tenant_id = uuid.uuid4()
    refund_id, _ = await _create_paid_invoice_and_refund_request(tool_registry, tenant_id)

    result = await tool_registry.execute("finance.approve_refund", {"refund_id": refund_id}, _human_ctx(tenant_id))
    assert result.refund["status"] == "COMPLETED"


async def test_ai_cannot_approve_an_invoice(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _human_ctx(tenant_id)
    customer = await tool_registry.execute("crm.create_customer", {"name": "Invoice Guard Customer"}, ctx)
    invoice = await tool_registry.execute(
        "finance.create_invoice_draft",
        {"customer_id": customer.customer["id"], "line_items": [{"description": "Job", "quantity": "1", "unit_price": "50000.00"}]},
        ctx,
    )
    invoice_id = invoice.invoice["id"]
    await tool_registry.execute("finance.request_invoice_approval", {"invoice_id": invoice_id}, ctx)

    with pytest.raises(ValueError, match="AI cannot approve an invoice"):
        await tool_registry.execute("finance.approve_invoice", {"invoice_id": invoice_id}, _ai_ctx(tenant_id))


async def test_ai_cannot_reject_an_invoice(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _human_ctx(tenant_id)
    customer = await tool_registry.execute("crm.create_customer", {"name": "Invoice Guard Customer 2"}, ctx)
    invoice = await tool_registry.execute(
        "finance.create_invoice_draft",
        {"customer_id": customer.customer["id"], "line_items": [{"description": "Job", "quantity": "1", "unit_price": "50000.00"}]},
        ctx,
    )
    invoice_id = invoice.invoice["id"]
    await tool_registry.execute("finance.request_invoice_approval", {"invoice_id": invoice_id}, ctx)

    with pytest.raises(ValueError, match="AI cannot reject an invoice"):
        await tool_registry.execute("finance.reject_invoice", {"invoice_id": invoice_id}, _ai_ctx(tenant_id))


async def test_ai_cannot_approve_a_credit_note(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _human_ctx(tenant_id)
    customer = await tool_registry.execute("crm.create_customer", {"name": "CN Guard Customer"}, ctx)
    invoice = await tool_registry.execute(
        "finance.create_invoice_draft",
        {"customer_id": customer.customer["id"], "line_items": [{"description": "Job", "quantity": "1", "unit_price": "300.00"}]},
        ctx,
    )
    invoice_id = invoice.invoice["id"]
    note = await tool_registry.execute(
        "finance.create_credit_note_request",
        {"invoice_id": invoice_id, "reason": "goodwill", "line_items": [{"description": "Goodwill credit", "amount": "50.00"}]},
        ctx,
    )

    with pytest.raises(ValueError, match="AI cannot approve a credit note"):
        await tool_registry.execute(
            "finance.approve_credit_note", {"credit_note_id": note.credit_note["id"]}, _ai_ctx(tenant_id)
        )


async def test_ai_cannot_approve_a_writeoff(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _human_ctx(tenant_id)
    customer = await tool_registry.execute("crm.create_customer", {"name": "WO Guard Customer"}, ctx)
    invoice = await tool_registry.execute(
        "finance.create_invoice_draft",
        {"customer_id": customer.customer["id"], "line_items": [{"description": "Job", "quantity": "1", "unit_price": "300.00"}]},
        ctx,
    )
    invoice_id = invoice.invoice["id"]
    writeoff = await tool_registry.execute(
        "finance.create_writeoff_request",
        {"invoice_id": invoice_id, "amount": "300.00", "reason": "uncollectable"},
        ctx,
    )

    with pytest.raises(ValueError, match="AI cannot approve a write-off"):
        await tool_registry.execute(
            "finance.approve_writeoff", {"writeoff_id": writeoff.writeoff["id"]}, _ai_ctx(tenant_id)
        )
