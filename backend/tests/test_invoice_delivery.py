"""Invoices are really e-mailed, and a partner clinic's invoice and reminders go through the clinic channel (a clinic is a business, so the patient consent guard does not apply)."""

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db.session import async_session_maker
from app.invoice_delivery.email_adapter import EmailInvoiceDeliveryAdapter, compose
from app.invoice_delivery.factory import get_invoice_delivery_provider
from app.invoice_delivery.internal_test_adapter import InternalTestInvoiceDeliveryAdapter
from app.models.communication import CommunicationLog
from app.models.crm import Customer
from app.models.finance import CollectionAction, CollectionActionStatus, Invoice, InvoiceStatus
from app.models.organization import Organization
from app.services.collection_service import CollectionService

pytestmark = pytest.mark.asyncio


class _Clinic:
    def __init__(self, ok: bool = True) -> None:
        self.sent, self.ok = [], ok

    async def send_email(self, tenant_id, **kw):
        self.sent.append(kw)
        return self.ok


class _Patients:
    """The consent-guarded channel: here it refuses everything, as it would for an address with no contact consent on record."""

    def __init__(self) -> None:
        self.calls = []

    async def deliver_email(self, tenant_id, **kw):
        self.calls.append(kw)
        return SimpleNamespace(blocked=True, sent=False)


async def _world(*, clinic: bool, email: str | None = "billing@apollo.example", currency: str = "EUR"):
    tid, cid = uuid.uuid4(), uuid.uuid4()
    async with async_session_maker() as s:
        s.add(Organization(id=tid, name="MedLuxe Travel", slug=f"ml-{tid.hex[:8]}"))
        s.add(Customer(id=cid, tenant_id=tid, name="Apollo Hospital", email=email, external_provider="mt_provider" if clinic else None, external_id=str(uuid.uuid4()) if clinic else None))
        inv = Invoice(tenant_id=tid, customer_id=cid, invoice_number="INV-9", status=InvoiceStatus.OVERDUE, currency=currency, issue_date=date(2026, 9, 1), due_date=date(2026, 9, 15),
                      subtotal=750, total=750, amount_paid=0, amount_due=Decimal("750"))
        s.add(inv)
        await s.commit()
        return tid, inv.id


def test_the_invoice_email_states_the_amount_in_the_invoice_currency_with_both_dates() -> None:
    inv = Invoice(invoice_number="INV-9", currency="EUR", amount_due=Decimal("750"), issue_date=date(2026, 9, 1), due_date=date(2026, 9, 15))
    subject, body = compose("MedLuxe Travel", "Apollo Hospital", inv)
    assert subject == "Invoice INV-9 from MedLuxe Travel" and "EUR 750.00" in body and "$" not in body
    assert "Issued: 2026-09-01" in body and "Due: 2026-09-15" in body and "reply to this email" in body and "Hello Apollo Hospital" in body


async def test_a_clinic_invoice_is_emailed_through_the_clinic_channel_not_the_patient_guard(monkeypatch) -> None:
    clinic, patients = _Clinic(), _Patients()
    monkeypatch.setattr("app.communications.factory.get_clinic_channel", lambda _sf: clinic)
    monkeypatch.setattr("app.communications.factory.get_communication_provider", lambda _sf: patients)
    tid, inv_id = await _world(clinic=True)
    out = await EmailInvoiceDeliveryAdapter(async_session_maker).send_invoice(tid, invoice_id=inv_id, customer_email="billing@apollo.example", amount=Decimal("750"), invoice_number="INV-9")
    assert out.delivered is True and len(clinic.sent) == 1 and patients.calls == []
    assert clinic.sent[0]["to"] == "billing@apollo.example" and "EUR 750.00" in clinic.sent[0]["body"] and clinic.sent[0]["subject"] == "Invoice INV-9 from MedLuxe Travel"


async def test_any_other_customer_goes_through_the_consent_guard_and_a_refusal_is_not_delivery(monkeypatch) -> None:
    clinic, patients = _Clinic(), _Patients()
    monkeypatch.setattr("app.communications.factory.get_clinic_channel", lambda _sf: clinic)
    monkeypatch.setattr("app.communications.factory.get_communication_provider", lambda _sf: patients)
    tid, inv_id = await _world(clinic=False, email="person@example.com")
    out = await EmailInvoiceDeliveryAdapter(async_session_maker).send_invoice(tid, invoice_id=inv_id, customer_email="person@example.com", amount=Decimal("750"), invoice_number="INV-9")
    assert out.delivered is False and clinic.sent == [] and len(patients.calls) == 1 and patients.calls[0]["customer_id"] is not None


async def test_a_customer_with_no_email_is_logged_and_reported_as_not_delivered(monkeypatch) -> None:
    clinic = _Clinic()
    monkeypatch.setattr("app.communications.factory.get_clinic_channel", lambda _sf: clinic)
    tid, inv_id = await _world(clinic=True, email=None)
    out = await EmailInvoiceDeliveryAdapter(async_session_maker).send_invoice(tid, invoice_id=inv_id, customer_email=None, amount=Decimal("750"), invoice_number="INV-9")
    assert out.delivered is False and clinic.sent == []
    async with async_session_maker() as s:
        logs = list((await s.execute(select(CommunicationLog).where(CommunicationLog.tenant_id == tid))).scalars())
    assert len(logs) == 1 and logs[0].status == "SENT_NO_EMAIL_ON_FILE"


async def test_a_failing_clinic_channel_is_not_reported_as_delivered(monkeypatch) -> None:
    monkeypatch.setattr("app.communications.factory.get_clinic_channel", lambda _sf: _Clinic(ok=False))
    tid, inv_id = await _world(clinic=True)
    out = await EmailInvoiceDeliveryAdapter(async_session_maker).send_invoice(tid, invoice_id=inv_id, customer_email="billing@apollo.example", amount=Decimal("750"), invoice_number="INV-9")
    assert out.delivered is False


async def test_the_real_adapter_is_used_only_when_an_email_provider_is_configured(monkeypatch) -> None:
    monkeypatch.setattr("app.invoice_delivery.factory.email_configured", lambda: False)
    assert isinstance(get_invoice_delivery_provider(async_session_maker), InternalTestInvoiceDeliveryAdapter)
    monkeypatch.setattr("app.invoice_delivery.factory.email_configured", lambda: True)
    assert isinstance(get_invoice_delivery_provider(async_session_maker), EmailInvoiceDeliveryAdapter)


async def test_a_clinic_reminder_uses_the_clinic_channel_and_a_failed_send_stays_pending(monkeypatch) -> None:
    clinic, patients = _Clinic(ok=False), _Patients()
    monkeypatch.setattr("app.communications.factory.get_clinic_channel", lambda _sf: clinic)
    tid, inv_id = await _world(clinic=True)
    service = CollectionService(async_session_maker, patients)  # the patient guard would refuse everything; a clinic must not depend on it
    await service.schedule_next_action(tid, inv_id, days_overdue=3)
    assert await service.execute_due_actions(tid) == [] and len(clinic.sent) == 1 and patients.calls == []   # failed send: nothing marked executed
    clinic.ok = True
    assert len(await service.execute_due_actions(tid)) == 1 and len(clinic.sent) == 2                         # retried the next run and delivered
    assert "EUR 750.00" in clinic.sent[1]["body"]
    async with async_session_maker() as s:
        actions = list((await s.execute(select(CollectionAction).where(CollectionAction.tenant_id == tid))).scalars())
    assert [a.status for a in actions] == [CollectionActionStatus.EXECUTED]


async def test_the_sent_notice_is_not_a_second_email_when_the_invoice_itself_was_emailed(monkeypatch) -> None:
    from app.events.bus import EventBus  # noqa: F401
    from app.events.finance_handlers import register_finance_handlers
    from app.models.event import Event, EventType

    tid, inv_id = await _world(clinic=False, email="person@example.com", currency="GBP")
    sent: list[dict] = []

    class _Comms:
        async def deliver_email(self, tenant_id, **kw):
            sent.append(kw)
            return SimpleNamespace(blocked=False, sent=True)

    handlers: dict = {}

    class _Bus:
        def subscribe(self, event_type, name, fn):
            handlers[name] = fn

    import app.events.finance_handlers as fh

    monkeypatch.setattr(fh, "get_communication_provider", lambda _sf: _Comms())
    register_finance_handlers(_Bus(), async_session_maker)
    notifier = handlers["finance_invoice_sent_notifier"]
    event = Event(tenant_id=tid, event_type=EventType.INVOICE_SENT, source="finance", entity_type="invoice", entity_id=inv_id, payload={"invoice_id": str(inv_id)})
    monkeypatch.setattr("app.communications.email_config.email_configured", lambda: True)
    await notifier(event)
    assert sent == []  # real delivery is on: the invoice email is the notice

    monkeypatch.setattr("app.communications.email_config.email_configured", lambda: False)
    await notifier(event)  # no real e-mail provider: the notice still goes through the consent-guarded channel, in the invoice's own currency
    assert len(sent) == 1 and "GBP 750" in sent[0]["body"] and "$" not in sent[0]["body"]
