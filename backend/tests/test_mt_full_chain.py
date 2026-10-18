"""The whole Medical Tourism business in one run, through the real API and services: a website enquiry with consent, its coordinator tasks, a clinic with agreed terms, the referral and the patient
being told, the clinic accepting, the reported outcome with a treatment price, the commission and the invoice drafted for the clinic, the invoice approved and sent, the invoice going overdue and the
clinic being reminded in the invoice's own currency, and the owner's morning digest. What is a stand-in: outgoing mail (recorded, never delivered) and the AI model."""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.events.mt_task_handlers import register_mt_task_handlers
from app.models.event import EventType
from app.models.finance import Invoice, InvoiceStatus
from app.models.medical_tourism import Provider
from app.models.medical_tourism_referral import HandoffTask
from app.services import owner_digest
from app.services.ar_service import ARService
from app.services.collection_service import CollectionService
from app.services.exception_service import ExceptionService
from tests.test_consent_gate_intake import _enable_vertical
from tests.test_mt_referrals import BASE, _approved, _FakeChannel, _gated, _h, _provider, halla  # noqa: F401

pytestmark = pytest.mark.asyncio


class _Patients:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def deliver_email(self, tenant_id, **kw):
        self.sent.append(kw)
        return SimpleNamespace(blocked=False, sent=True)


async def test_the_business_runs_from_a_website_enquiry_to_an_overdue_invoice_reminder_and_the_owner_digest(client, event_bus, halla, monkeypatch) -> None:  # noqa: F811
    s = get_settings()
    clinic_mail, patient_mail, owner_mail = _FakeChannel(), _Patients(), _FakeChannel()
    monkeypatch.setattr(s, "MT_REFERRAL_TRANSPORT", "email")
    monkeypatch.setattr(s, "SENDGRID_API_KEY", "key-for-test")
    monkeypatch.setattr(s, "SENDGRID_FROM_EMAIL", "from@klaros.example")
    monkeypatch.setattr("app.communications.factory.get_clinic_channel", lambda _sf: clinic_mail)
    monkeypatch.setattr("app.communications.factory.get_communication_provider", lambda _sf: patient_mail)
    monkeypatch.setattr("app.communications.factory.get_internal_channel", lambda _sf: owner_mail)

    # --- the business is registered, handles patient data and has the Medical Tourism module
    token, tid = await _gated(client, "Full Chain Travel", "fullchain@example.com")
    register_mt_task_handlers(event_bus, async_session_maker)
    await _enable_vertical(tid)
    await event_bus.process_pending(EventType.LEAD_CREATED)

    # --- a verified clinic with its own contact address and an agreed commission (15% of the treatment price)
    clinic = await _provider(client, token, "Bosphorus Dental", "TR")
    async with async_session_maker() as session:
        await set_tenant_context(session, tid)
        (await session.get(Provider, __import__("uuid").UUID(clinic["id"]))).contact_email = "billing@bosphorus.example"
        await session.commit()
    terms = await client.put(f"{BASE}/providers/{clinic['id']}/terms", json={"commission_basis": "PERCENTAGE", "commission_percentage": "15", "currency": "eur", "agreement_reference": "Folder 3"}, headers=_h(token))
    assert terms.status_code == 200, terms.text

    # --- a patient fills in the website form, with consent: a lead and a coordinator task appear
    form = {"name": "Sam Visitor", "source": "WEB", "email": "sam@example.com", "service_requested": "Dental", "location": "Turkey",
            "consent": {"scopes": ["store_personal_data", "store_medical_information", "contact"], "wording_version": "owner-label-v1"}}
    made = await client.post(f"/api/v1/public/leads/{tid}", json=form)
    assert made.status_code == 201, made.text
    lead = made.json()["lead_id"]
    await event_bus.process_pending(EventType.LEAD_CREATED)
    async with async_session_maker() as session:
        await set_tenant_context(session, tid)
        tasks = list((await session.execute(select(HandoffTask).where(HandoffTask.tenant_id == tid))).scalars())
    assert [t.kind for t in tasks] == ["new_enquiry"] and tasks[0].status == "OPEN"

    # --- the patient approves this clinic; the referral is sent; the clinic is told and so is the patient
    referral = await _approved(client, token, lead, clinic["id"])
    assert (await client.post(f"{BASE}/referrals/{referral}/send", headers=_h(token))).status_code == 200
    assert len(clinic_mail.sent) == 1 and clinic_mail.sent[0]["to"] == "billing@bosphorus.example"
    assert [m["subject"] for m in patient_mail.sent] == ["Your enquiry has been passed to Bosphorus Dental"] and patient_mail.sent[0]["to"] == "sam@example.com"
    commissions = (await client.get(f"{BASE}/commissions", headers=_h(token))).json()["commissions"]
    assert len(commissions) == 1 and commissions[0]["status"] == "PENDING"          # created from the agreed terms, as a potential only
    async with async_session_maker() as session:
        await set_tenant_context(session, tid)
        assert all(t.status == "DONE" for t in (await session.execute(select(HandoffTask).where(HandoffTask.tenant_id == tid, HandoffTask.kind == "new_enquiry"))).scalars())

    # --- the clinic accepts, treats the patient and reports it with a price: the commission is confirmed and an invoice is drafted for the clinic
    assert (await client.post(f"{BASE}/referrals/{referral}/clinic-response", json={"accepted": True}, headers=_h(token))).status_code == 200
    assert patient_mail.sent[-1]["subject"] == "Bosphorus Dental has accepted your enquiry"
    done = await client.post(f"{BASE}/referrals/{referral}/outcome", json={"outcome": "TREATMENT_COMPLETED", "evidence": "clinic email, 12 Nov", "treatment_amount": "4000", "currency": "eur"}, headers=_h(token))
    assert done.status_code == 200, done.text
    invoices = (await client.get("/api/v1/invoices", headers=_h(token))).json()["invoices"]
    assert len(invoices) == 1 and invoices[0]["status"] == "DRAFT" and invoices[0]["currency"] == "EUR" and float(invoices[0]["total"]) == 600.0   # 15% of 4000
    invoice_id = invoices[0]["id"]

    # --- a person approves and sends it
    requested = await client.post(f"/api/v1/invoices/{invoice_id}/request-approval", headers=_h(token))
    assert requested.status_code == 200, requested.text
    if requested.json()["invoice"]["status"] != "APPROVED":  # a small invoice is approved on request; a larger one waits for a person
        approved = await client.post(f"/api/v1/invoices/{invoice_id}/approve", headers=_h(token))
        assert approved.status_code == 200, approved.text
    sent = await client.post(f"/api/v1/invoices/{invoice_id}/send", headers=_h(token))
    assert sent.status_code == 200 and sent.json()["invoice"]["status"] == "SENT", sent.text

    # --- 30 days pass without payment: it goes overdue and the clinic is reminded, in euros, once for this stage
    import uuid

    async with async_session_maker() as session:
        await set_tenant_context(session, tid)
        inv = await session.get(Invoice, uuid.UUID(invoice_id))
        inv.due_date = date.today() - timedelta(days=3)
        await session.commit()
    overdue = ARService(async_session_maker, ExceptionService(async_session_maker, event_bus), CollectionService(async_session_maker, patient_mail))
    assert [str(i) for i in await overdue.detect_overdue(tid)] == [invoice_id]
    runner = CollectionService(async_session_maker, patient_mail)
    before = len(clinic_mail.sent)
    assert len(await runner.execute_due_actions(tid)) == 1
    reminder = clinic_mail.sent[before]
    assert reminder["to"] == "billing@bosphorus.example" and "EUR 600.00" in reminder["body"] and "$" not in reminder["body"]
    assert await runner.execute_due_actions(tid) == []                                  # never twice for the same stage
    async with async_session_maker() as session:
        await set_tenant_context(session, tid)
        assert (await session.get(Invoice, uuid.UUID(invoice_id))).status == InvoiceStatus.OVERDUE

    # --- and the owner's morning digest lists what needs a person
    monkeypatch.setattr("app.communications.email_config.email_configured", lambda: True)
    digest = await owner_digest.send_digest(async_session_maker, tid)
    assert digest["sent"] == 1 and owner_mail.sent[-1]["to"] == "fullchain@example.com"
