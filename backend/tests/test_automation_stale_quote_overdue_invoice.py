"""Rule 18/20 worked examples: Stale Quote and Overdue Invoice — a
two-automation composition using ONLY real, already-existing domain
sweeps and events, no fabrication:

  Automation 1 (SCHEDULE, daily) -> action: the existing deterministic
  sweep tool (`quotes.detect_expired_quotes` / `finance.detect_overdue_invoices`)
  -> the sweep itself publishes a REAL, already-existing event
  (`quote.expired` / `exception.created`) exactly as it always has.

  Automation 2 (EVENT, keyed off that real event) -> condition (for the
  invoice case, filters to INVOICE_OVERDUE exceptions specifically, since
  exception.created fires for every exception type) -> action:
  `notifications.create_notification` to alert the owner.

No new EventType was added for this — both events already existed and are
already published by the pre-existing QuoteService/ARService/ExceptionService
sweeps; the Automation Engine only orchestrates calling them and reacting.
"""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.ai.execution_service import AIExecutionService
from app.db.session import async_session_maker
from app.models.actor import ActorType
from app.models.automation import AutomationExecution, ExecutionStatus, TriggerType
from app.models.event import EventType
from app.models.finance import Invoice, InvoiceStatus
from app.models.notification import Notification
from app.models.quote import Quote
from app.models.rbac import Role
from app.services.automation_service import AutomationService
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id: uuid.UUID) -> ExecutionContext:
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=Role.OWNER)


def _service(tool_registry) -> AutomationService:
    return AutomationService(async_session_maker, AIExecutionService(tool_registry))


async def _make_customer(tool_registry, tenant_id: uuid.UUID) -> str:
    result = await tool_registry.execute(
        "crm.create_customer", {"name": "Worked Example Customer", "email": "worked-example@example.com"}, _ctx(tenant_id),
    )
    return result.customer["id"]


async def test_stale_quote_worked_example_end_to_end(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    service = _service(tool_registry)

    # Seed a real, genuinely-expired quote (same pattern as
    # tests/test_quotes.py::test_detect_expired_quotes_sweep).
    customer_id = await _make_customer(tool_registry, tenant_id)
    created = await tool_registry.execute(
        "quotes.create_quote_draft",
        {"customer_id": customer_id, "line_items": [{"description": "Roof repair", "quantity": "1", "unit_price": "500.00"}]},
        ctx,
    )
    await tool_registry.execute("quotes.send_quote", {"quote_id": created.quote["id"]}, ctx)
    async with async_session_maker() as session:
        row = await session.get(Quote, uuid.UUID(created.quote["id"]))
        row.valid_until = date.today() - timedelta(days=1)
        await session.commit()

    # Automation 1: SCHEDULE -> sweep for expired quotes.
    sweep_automation = await service.create_automation(
        tenant_id, name="Daily Stale Quote Sweep", description=None, trigger_type=TriggerType.SCHEDULE,
        trigger_config={"frequency": "DAILY", "time": "08:00"}, condition=None,
        steps=[{"action": "quotes.detect_expired_quotes", "params": {}}], created_by=None,
    )
    await service.publish(tenant_id, sweep_automation.id)

    # Automation 2: EVENT (quote.expired) -> notify owner.
    notify_automation = await service.create_automation(
        tenant_id, name="Notify Owner of Expired Quote", description=None, trigger_type=TriggerType.EVENT,
        trigger_config={"event_type": EventType.QUOTE_EXPIRED}, condition=None,
        steps=[{"action": "notifications.create_notification", "params": {"title": "Quote expired", "body": "A quote went stale"}}],
        created_by=None,
    )
    await service.publish(tenant_id, notify_automation.id)

    dispatched = await service.check_and_dispatch_scheduled(tenant_id, now_utc=datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc))
    assert len(dispatched) == 1

    sweep_execution = await service.get_execution(tenant_id, dispatched[0])
    assert sweep_execution.status == ExecutionStatus.COMPLETED
    sweep_steps = await service.list_execution_steps(tenant_id, dispatched[0])
    assert sweep_steps[0].status == "SUCCEEDED"
    assert len(sweep_steps[0].result["expired_quote_ids"]) == 1

    async with async_session_maker() as session:
        quote = await session.get(Quote, uuid.UUID(created.quote["id"]))
        assert quote.status == "EXPIRED"

    stats = await event_bus.process_pending(EventType.QUOTE_EXPIRED)
    assert stats.succeeded >= 1

    async with async_session_maker() as session:
        notify_executions = (
            await session.execute(select(AutomationExecution).where(AutomationExecution.automation_id == notify_automation.id))
        ).scalars().all()
    assert len(notify_executions) == 1
    assert notify_executions[0].status == ExecutionStatus.COMPLETED

    async with async_session_maker() as session:
        notifications = (
            await session.execute(select(Notification).where(Notification.tenant_id == tenant_id, Notification.title == "Quote expired"))
        ).scalars().all()
    assert len(notifications) == 1


async def test_overdue_invoice_worked_example_end_to_end(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    service = _service(tool_registry)

    customer_id = await _make_customer(tool_registry, tenant_id)
    async with async_session_maker() as session:
        invoice = Invoice(
            tenant_id=tenant_id, invoice_number=f"INV-{uuid.uuid4().hex[:8]}", customer_id=uuid.UUID(customer_id),
            status=InvoiceStatus.SENT, issue_date=date.today() - timedelta(days=40),
            due_date=date.today() - timedelta(days=20), total=500, amount_due=500,
            sent_at=datetime.now(timezone.utc) - timedelta(days=40),
        )
        session.add(invoice)
        await session.commit()
        invoice_id = invoice.id

    # Automation 1: SCHEDULE -> sweep for overdue invoices.
    sweep_automation = await service.create_automation(
        tenant_id, name="Daily Overdue Invoice Sweep", description=None, trigger_type=TriggerType.SCHEDULE,
        trigger_config={"frequency": "DAILY", "time": "08:00"}, condition=None,
        steps=[{"action": "finance.detect_overdue_invoices", "params": {}}], created_by=None,
    )
    await service.publish(tenant_id, sweep_automation.id)

    # Automation 2: EVENT (exception.created), gated to INVOICE_OVERDUE only
    # (exception.created fires for every exception type) -> notify owner.
    notify_automation = await service.create_automation(
        tenant_id, name="Notify Owner of Overdue Invoice", description=None, trigger_type=TriggerType.EVENT,
        trigger_config={"event_type": EventType.EXCEPTION_CREATED},
        condition={"field": "invoice.type", "op": "eq", "value": "INVOICE_OVERDUE"},
        steps=[{"action": "notifications.create_notification", "params": {"title": "Invoice overdue", "body": "Collections needed"}}],
        created_by=None,
    )
    await service.publish(tenant_id, notify_automation.id)

    dispatched = await service.check_and_dispatch_scheduled(tenant_id, now_utc=datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc))
    assert len(dispatched) == 1

    sweep_steps = await service.list_execution_steps(tenant_id, dispatched[0])
    assert sweep_steps[0].status == "SUCCEEDED"
    assert str(invoice_id) in sweep_steps[0].result["newly_overdue_invoice_ids"]

    async with async_session_maker() as session:
        invoice = await session.get(Invoice, invoice_id)
        assert invoice.status == InvoiceStatus.OVERDUE

    stats = await event_bus.process_pending(EventType.EXCEPTION_CREATED)
    assert stats.succeeded >= 1

    async with async_session_maker() as session:
        notify_executions = (
            await session.execute(select(AutomationExecution).where(AutomationExecution.automation_id == notify_automation.id))
        ).scalars().all()
    assert len(notify_executions) == 1
    assert notify_executions[0].status == ExecutionStatus.COMPLETED

    async with async_session_maker() as session:
        notifications = (
            await session.execute(select(Notification).where(Notification.tenant_id == tenant_id, Notification.title == "Invoice overdue"))
        ).scalars().all()
    assert len(notifications) == 1


async def test_overdue_invoice_condition_filters_out_unrelated_exception_types(event_bus, tool_registry) -> None:
    """The condition on exception.type must genuinely gate — a non-invoice
    exception (e.g. a job exception) must never trigger the notification."""
    tenant_id = uuid.uuid4()
    service = _service(tool_registry)

    notify_automation = await service.create_automation(
        tenant_id, name="Invoice-only Notify", description=None, trigger_type=TriggerType.EVENT,
        trigger_config={"event_type": EventType.EXCEPTION_CREATED},
        condition={"field": "invoice.type", "op": "eq", "value": "INVOICE_OVERDUE"},
        steps=[{"action": "notifications.create_notification", "params": {"title": "Should not fire", "body": "x"}}],
        created_by=None,
    )
    await service.publish(tenant_id, notify_automation.id)

    await event_bus.publish(
        tenant_id=tenant_id, event_type=EventType.EXCEPTION_CREATED, source="operations", entity_type="job",
        entity_id=uuid.uuid4(), payload={"exception_id": str(uuid.uuid4()), "type": "JOB_BLOCKED", "severity": "MEDIUM"},
    )
    await event_bus.process_pending(EventType.EXCEPTION_CREATED)

    async with async_session_maker() as session:
        notifications = (
            await session.execute(select(Notification).where(Notification.tenant_id == tenant_id, Notification.title == "Should not fire"))
        ).scalars().all()
    assert notifications == []
