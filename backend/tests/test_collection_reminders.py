"""Overdue invoice reminders say what is owed in the invoice's own currency, once per stage, and the clinic-invoice crew runs them without a model."""

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.db.session import async_session_maker
from app.models.crm import Customer
from app.models.finance import CollectionActionType, Invoice, InvoiceStatus
from app.models.organization import Organization
from app.services import operating_crew as oc
from app.services.collection_service import CollectionService, reminder_body

pytestmark = pytest.mark.asyncio


class _Comms:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def deliver_email(self, tenant_id, **kw):
        self.sent.append(kw)
        return SimpleNamespace(blocked=False)


def _invoice(**over) -> Invoice:
    base = dict(invoice_number="INV-7", currency="EUR", amount_due=Decimal("1250.5"), due_date=date(2026, 9, 30))
    return Invoice(**{**base, **over})


def test_the_reminder_uses_the_invoice_currency_and_never_a_dollar_sign() -> None:
    body = reminder_body("Artemis Hospital", _invoice(), CollectionActionType.FRIENDLY_REMINDER)
    assert "EUR 1,250.50" in body and "$" not in body and "2026-09-30" in body and "Artemis Hospital" in body
    second = reminder_body(None, _invoice(), CollectionActionType.SECOND_REMINDER)
    assert second.startswith("Hello there") and "earlier reminder" in second


async def test_an_overdue_invoice_gets_one_reminder_per_stage_and_none_twice() -> None:
    tid, cid = uuid.uuid4(), uuid.uuid4()
    async with async_session_maker() as s:
        s.add(Organization(id=tid, name="Reminder Co", slug=f"rc-{tid.hex[:8]}"))
        s.add(Customer(id=cid, tenant_id=tid, name="Apollo Hospital", email="billing@apollo.example"))
        inv = Invoice(tenant_id=tid, customer_id=cid, invoice_number="INV-1", status=InvoiceStatus.OVERDUE, currency="EUR", issue_date=date(2026, 9, 1), due_date=date(2026, 9, 15),
                      subtotal=500, total=500, amount_paid=0, amount_due=500)
        s.add(inv)
        await s.commit()
        invoice_id = inv.id
    comms = _Comms()
    service = CollectionService(async_session_maker, comms)
    await service.schedule_next_action(tid, invoice_id, days_overdue=3)
    assert len(await service.execute_due_actions(tid)) == 1
    assert len(comms.sent) == 1 and comms.sent[0]["to"] == "billing@apollo.example" and "EUR 500.00" in comms.sent[0]["body"]
    await service.schedule_next_action(tid, invoice_id, days_overdue=3)  # the same stage again: nothing new to send
    assert await service.execute_due_actions(tid) == [] and len(comms.sent) == 1
    await service.schedule_next_action(tid, invoice_id, days_overdue=20)  # the next stage sends the second reminder
    assert len(await service.execute_due_actions(tid)) == 1 and len(comms.sent) == 2


async def test_the_clinic_finance_crew_detects_and_chases_overdue_invoices_with_no_model() -> None:
    crew = {m.key: m for m in oc.MEDICAL_TOURISM_CREW}
    detector, runner = crew["overdue-detector"], crew["collections-runner"]
    assert oc._triggers(detector)["schedule"]["action"] == {"tool_name": "finance.detect_overdue_invoices", "tool_input": {}}
    assert oc._triggers(runner)["schedule"]["action"] == {"tool_name": "finance.execute_due_collection_actions", "tool_input": {}}
    assert detector.schedule_time < runner.schedule_time  # overdue is marked before reminders go out
    assert not {"finance.void_invoice", "finance.write_off_invoice", "finance.refund_invoice"} & set(detector.tools + runner.tools)
