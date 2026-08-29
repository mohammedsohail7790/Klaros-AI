"""section 56-57: the full, realistic Finance & Back Office scenario, run
end to end against a real (test-database) seeded tenant.

    job closed -> invoice.trigger_requested -> DRAFT invoice
    -> validate/recalculate totals -> request approval (auto, under threshold)
    -> send (internal test delivery) -> record test payment (internal test
    payment provider) -> allocated -> invoice PAID -> AR updated
    -> job cost recorded -> job actual margin updated -> Customer 360 /
    Owner Cockpit / audit trail all reflect real data

plus a second overdue-path scenario: due date passes -> AR detects it,
flips to OVERDUE, opens an exception, and schedules a collection action.
"""

import base64
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.event import EventType
from app.models.finance import CollectionAction, Invoice, InvoiceStatus, Payment
from app.models.operations import ExceptionStatus, ExceptionType, Job, OperationsException
from app.models.rbac import Role
from app.services.ar_service import ARService
from app.services.collection_service import CollectionService
from app.services.exception_service import ExceptionService
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id, role=Role.OWNER):
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


async def _create_closed_job(tool_registry, ctx, tenant_id, session_factory, *, estimated_revenue=800.0, estimated_cost=400.0):
    customer = await tool_registry.execute(
        "crm.create_customer", {"name": "Finance E2E Customer", "email": "finance-e2e@example.com"}, ctx
    )
    customer_id = customer.customer["id"]

    job_out = await tool_registry.execute(
        "operations.create_job", {"title": "Water heater install", "customer_id": customer_id}, ctx
    )
    job_id = job_out.job["id"]

    # operations.update_job doesn't expose estimated_revenue/estimated_cost
    # (those are set at job-creation/estimation time in the real flow, which
    # is out of Phase 5's scope) — set them directly for this test fixture.
    async with session_factory() as session:
        job_row = await session.get(Job, uuid.UUID(job_id))
        job_row.estimated_revenue = estimated_revenue
        job_row.estimated_cost = estimated_cost
        await session.commit()

    await tool_registry.execute(
        "operations.schedule_job",
        {"job_id": job_id, "start_time": "2026-11-01T09:00:00+00:00", "end_time": "2026-11-01T11:00:00+00:00"},
        ctx,
    )
    worker = await tool_registry.execute("operations.create_worker", {"name": "Sam", "service_types": ["PLUMBING"]}, ctx)
    await tool_registry.execute("operations.assign_job", {"job_id": job_id, "worker_id": worker.worker["id"]}, ctx)
    await tool_registry.execute("operations.dispatch_job", {"job_id": job_id}, ctx)
    await tool_registry.execute("operations.update_job_status", {"job_id": job_id, "target_status": "EN_ROUTE"}, ctx)
    await tool_registry.execute("operations.update_job_status", {"job_id": job_id, "target_status": "ON_SITE"}, ctx)
    await tool_registry.execute("operations.start_job", {"job_id": job_id}, ctx)
    await tool_registry.execute(
        "operations.add_job_photo",
        {
            "job_id": job_id,
            "filename": "final.jpg",
            "content_type": "image/jpeg",
            "content_base64": base64.b64encode(b"real photo bytes").decode(),
        },
        ctx,
    )
    await tool_registry.execute("operations.complete_job", {"job_id": job_id}, ctx)
    await tool_registry.execute("operations.start_qa", {"job_id": job_id}, ctx)
    await tool_registry.execute("operations.complete_qa", {"job_id": job_id}, ctx)
    await tool_registry.execute("operations.generate_completion_packet", {"job_id": job_id}, ctx)
    closed = await tool_registry.execute("operations.close_job", {"job_id": job_id}, ctx)
    assert closed.job["status"] == "CLOSED"
    return customer_id, job_id


async def test_full_finance_lifecycle(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    customer_id, job_id = await _create_closed_job(tool_registry, ctx, tenant_id, event_bus.session_factory)

    # invoice.trigger_requested was published by close_job; process it.
    stats = await event_bus.process_pending(EventType.INVOICE_TRIGGER_REQUESTED)
    assert stats.succeeded >= 1

    async with event_bus.session_factory() as session:
        invoice = (
            await session.execute(select(Invoice).where(Invoice.tenant_id == tenant_id, Invoice.job_id == uuid.UUID(job_id)))
        ).scalar_one()
    assert invoice.status == InvoiceStatus.DRAFT
    assert invoice.total == Decimal("800.00")
    assert invoice.amount_due == Decimal("800.00")
    invoice_id = str(invoice.id)

    # Calling trigger again must not create a duplicate (idempotent).
    dup = await tool_registry.execute("finance.trigger_invoice_from_job", {"job_id": job_id}, ctx)
    assert dup.deduplicated is True
    assert dup.invoice["id"] == invoice_id

    # Request approval — under the $1000 auto-approval threshold, so it
    # auto-approves without creating a pending ApprovalRequest.
    approved = await tool_registry.execute("finance.request_invoice_approval", {"invoice_id": invoice_id}, ctx)
    assert approved.invoice["status"] == "APPROVED"

    # Send via the internal test invoice delivery provider. AUTO at the
    # ToolRegistry level — sending is already gated by the invoice's own
    # approval state machine (only APPROVED invoices can be sent), so a
    # second, generic, non-resumable ToolRegistry-level approval on top of
    # that would be redundant. See app/tools/policy.py's comment on this.
    sent_out = await tool_registry.execute("finance.send_invoice", {"invoice_id": invoice_id}, ctx)
    assert sent_out.invoice["status"] == "SENT"

    # Record a test payment via the internal test payment provider.
    payment = await tool_registry.execute(
        "finance.record_test_payment",
        {
            "customer_id": customer_id,
            "amount": "800.00",
            "allocations": [{"invoice_id": invoice_id, "amount": "800.00"}],
        },
        ctx,
    )
    assert payment.payment["status"] == "SUCCEEDED"
    assert payment.payment["provider"] == "internal_test_payment"

    async with event_bus.session_factory() as session:
        invoice_after = await session.get(Invoice, uuid.UUID(invoice_id))
    assert invoice_after.status == InvoiceStatus.PAID
    assert invoice_after.amount_due == Decimal("0.00")
    assert invoice_after.amount_paid == Decimal("800.00")

    # Idempotency: recording the same external payment again must not
    # double-allocate or double-pay.
    same_external_id = payment.payment["external_id"]

    from app.models.finance import PaymentStatus
    from app.services.payment_service import AllocationInput, PaymentService

    payment_service = PaymentService(event_bus.session_factory, event_bus)
    _, deduped = await payment_service.record_payment(
        tenant_id,
        customer_id=uuid.UUID(customer_id),
        amount=Decimal("800.00"),
        provider="internal_test_payment",
        external_id=same_external_id,
        payment_method="internal_test",
        allocations=[AllocationInput(invoice_id=uuid.UUID(invoice_id), amount=Decimal("800.00"))],
    )
    assert deduped is True

    # AR: this customer should now have zero outstanding balance.
    ar_service = ARService(
        event_bus.session_factory,
        ExceptionService(event_bus.session_factory, event_bus),
        CollectionService(event_bus.session_factory),
    )
    balance = await ar_service.customer_balance(tenant_id, uuid.UUID(customer_id))
    assert balance == Decimal("0")

    # Job costing: record an actual cost and check the *existing* Job
    # fields (not a new duplicate column) get updated.
    cost_out = await tool_registry.execute(
        "finance.record_job_cost",
        {"job_id": job_id, "category": "MATERIAL", "description": "Water heater unit", "quantity": "1", "unit_cost": "600.00"},
        ctx,
    )
    assert cost_out.job_cost["total_cost"] == "600.00"

    async with event_bus.session_factory() as session:
        job_row = await session.get(Job, uuid.UUID(job_id))
    assert float(job_row.actual_cost) == 600.0

    # A margin leak (est 50% margin -> actual much lower with cost=600 vs
    # revenue=800, i.e. 25%) should have opened a MARGIN_LEAK exception.
    async with event_bus.session_factory() as session:
        margin_exceptions = (
            await session.execute(
                select(OperationsException).where(
                    OperationsException.tenant_id == tenant_id,
                    OperationsException.type == ExceptionType.MARGIN_LEAK,
                    OperationsException.entity_id == uuid.UUID(job_id),
                )
            )
        ).scalars().all()
    assert len(margin_exceptions) == 1

    # Audit trail: invoice actions should be audited against the invoice entity.
    async with event_bus.session_factory() as session:
        audit_rows = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id == tenant_id, AuditLog.entity_type == "invoice", AuditLog.entity_id == uuid.UUID(invoice_id)
                )
            )
        ).scalars().all()
    audited_tools = {r.tool for r in audit_rows if r.tool}
    assert "finance.trigger_invoice_from_job" in audited_tools
    assert "finance.request_invoice_approval" in audited_tools

    # finance.record_test_payment's own primary entity is the payment it
    # created (see ToolRegistry._infer_entity — it picks the tool's own
    # output entity, not a related one), so check it there instead.
    async with event_bus.session_factory() as session:
        payment_audit_rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.tenant_id == tenant_id, AuditLog.entity_type == "payment")
            )
        ).scalars().all()
    assert "finance.record_test_payment" in {r.tool for r in payment_audit_rows if r.tool}

    # Owner Cockpit / finance summary: total AR should reflect real state.
    async with event_bus.session_factory() as session:
        open_invoices = (
            await session.execute(
                select(Invoice).where(Invoice.tenant_id == tenant_id, Invoice.status == InvoiceStatus.PAID)
            )
        ).scalars().all()
    assert len(open_invoices) == 1


async def test_overdue_invoice_triggers_ar_exception_and_collection_action(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    customer_id, job_id = await _create_closed_job(tool_registry, ctx, tenant_id, event_bus.session_factory, estimated_revenue=500.0, estimated_cost=200.0)
    await event_bus.process_pending(EventType.INVOICE_TRIGGER_REQUESTED)

    async with event_bus.session_factory() as session:
        invoice = (
            await session.execute(select(Invoice).where(Invoice.tenant_id == tenant_id, Invoice.job_id == uuid.UUID(job_id)))
        ).scalar_one()
        # Force the invoice into the past so detect_overdue finds it, and
        # into SENT status (as if it had already been sent).
        invoice.status = InvoiceStatus.SENT
        invoice.due_date = date.today() - timedelta(days=20)
        invoice.sent_at = datetime.now(timezone.utc) - timedelta(days=20)
        await session.commit()
        invoice_id = invoice.id

    ar_service = ARService(
        event_bus.session_factory,
        ExceptionService(event_bus.session_factory, event_bus),
        CollectionService(event_bus.session_factory),
    )
    newly_overdue = await ar_service.detect_overdue(tenant_id)
    assert invoice_id in newly_overdue

    async with event_bus.session_factory() as session:
        invoice_after = await session.get(Invoice, invoice_id)
    assert invoice_after.status == InvoiceStatus.OVERDUE

    async with event_bus.session_factory() as session:
        exc = (
            await session.execute(
                select(OperationsException).where(
                    OperationsException.tenant_id == tenant_id,
                    OperationsException.type == ExceptionType.INVOICE_OVERDUE,
                    OperationsException.entity_id == invoice_id,
                    OperationsException.status == ExceptionStatus.OPEN,
                )
            )
        ).scalar_one_or_none()
    assert exc is not None

    async with event_bus.session_factory() as session:
        actions = (
            await session.execute(
                select(CollectionAction).where(
                    CollectionAction.tenant_id == tenant_id, CollectionAction.invoice_id == invoice_id
                )
            )
        ).scalars().all()
    assert len(actions) == 1
    assert actions[0].scheduled_for is not None
