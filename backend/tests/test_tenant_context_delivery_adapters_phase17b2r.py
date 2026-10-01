"""Phase 17B-2R: real-PostgreSQL behavioral proof that the independently-
opened sessions in the communication / invoice-delivery / calendar
"internal test" and real-provider adapters now stamp `SET LOCAL
app.tenant_id`. These were the last remaining ZERO_CTX gaps found in
round 12 — missed by earlier rounds' `grep -v test` filename filtering,
since these adapter files are named `internal_test_adapter.py` (a
production file, not a pytest test file) and so were silently excluded
from that grep. Covers:

- app/communications/internal_test_adapter.py (1 site)
- app/communications/twilio_adapter.py (1 site, real-provider — httpx call mocked)
- app/communications/sendgrid_adapter.py (1 site, real-provider — httpx call mocked)
- app/invoice_delivery/internal_test_adapter.py (2 sites)
- app/calendar/internal_test_adapter.py (3 sites)
"""

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from app.calendar.base import BookingRequest
from app.calendar.internal_test_adapter import InternalTestCalendarAdapter
from app.communications.base import MessageTemplate
from app.communications.internal_test_adapter import InternalTestCommunicationAdapter
from app.communications.sendgrid_adapter import SendGridEmailAdapter
from app.communications.twilio_adapter import TwilioSMSAdapter
from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.invoice_delivery.internal_test_adapter import InternalTestInvoiceDeliveryAdapter
from app.models.crm import Customer
from app.models.organization import Organization

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)


class _ContextSpy:
    def __init__(self):
        self.calls: list[tuple] = []

    async def __call__(self, session, tenant_id):
        await set_tenant_context(session, tenant_id)
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            readback = await session.scalar(text("select current_setting('app.tenant_id', true)"))
        else:
            readback = None
        self.calls.append((tenant_id, readback))


async def _make_org(tenant_id: uuid.UUID) -> None:
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        await session.commit()


async def _make_customer(tenant_id: uuid.UUID) -> uuid.UUID:
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Adapter Test Customer")
        session.add(customer)
        await session.commit()
        await session.refresh(customer)
        return customer.id


@requires_real_postgres
async def test_internal_test_communication_adapter_sets_tenant_context(monkeypatch) -> None:
    import app.communications.internal_test_adapter as mod

    spy = _ContextSpy()
    monkeypatch.setattr(mod, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    adapter = InternalTestCommunicationAdapter(async_session_maker)

    assert await adapter.send_email(tenant_id, to="a@example.com", subject="hi", body="b", template=MessageTemplate.LEAD_FOLLOW_UP)
    assert await adapter.send_sms(tenant_id, to="+15550000000", body="b", template=MessageTemplate.LEAD_FOLLOW_UP)

    assert len(spy.calls) == 2
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_twilio_adapter_sets_tenant_context(monkeypatch) -> None:
    import app.communications.twilio_adapter as mod

    spy = _ContextSpy()
    monkeypatch.setattr(mod, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    adapter = TwilioSMSAdapter(async_session_maker, account_sid="AC_test", auth_token="tok", from_number="+15551234567")

    fake_response = AsyncMock()
    fake_response.raise_for_status = lambda: None
    fake_response.json = lambda: {"sid": "SM123"}
    fake_response.status_code = 201
    with patch("httpx.AsyncClient.post", return_value=fake_response):
        sent = await adapter.send_sms(tenant_id, to="+15559990000", body="hello", template=MessageTemplate.LEAD_FOLLOW_UP)

    assert sent is True
    assert len(spy.calls) == 1
    called_tenant, readback = spy.calls[0]
    assert called_tenant == tenant_id
    assert readback == str(tenant_id)


@requires_real_postgres
async def test_sendgrid_adapter_sets_tenant_context(monkeypatch) -> None:
    import app.communications.sendgrid_adapter as mod

    spy = _ContextSpy()
    monkeypatch.setattr(mod, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    adapter = SendGridEmailAdapter(async_session_maker, api_key="SG_test", from_email="from@example.com")

    fake_response = AsyncMock()
    fake_response.raise_for_status = lambda: None
    fake_response.headers = {"X-Message-Id": "msg123"}
    fake_response.status_code = 202
    with patch("httpx.AsyncClient.post", return_value=fake_response):
        sent = await adapter.send_email(tenant_id, to="a@example.com", subject="hi", body="b", template=MessageTemplate.LEAD_FOLLOW_UP)

    assert sent is True
    assert len(spy.calls) == 1
    called_tenant, readback = spy.calls[0]
    assert called_tenant == tenant_id
    assert readback == str(tenant_id)


@requires_real_postgres
async def test_invoice_delivery_internal_test_adapter_sets_tenant_context(monkeypatch) -> None:
    import app.invoice_delivery.internal_test_adapter as mod

    spy = _ContextSpy()
    monkeypatch.setattr(mod, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    adapter = InternalTestInvoiceDeliveryAdapter(async_session_maker)

    await adapter.send_invoice(
        tenant_id, invoice_id=uuid.uuid4(), customer_email="a@example.com", amount=Decimal("10.00"), invoice_number="INV-1"
    )
    await adapter.send_quote(
        tenant_id,
        quote_id=uuid.uuid4(),
        customer_email="a@example.com",
        amount=Decimal("10.00"),
        quote_number="Q-1",
        view_url="https://example.com/q/1",
    )

    assert len(spy.calls) == 2
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_internal_test_calendar_adapter_sets_tenant_context(monkeypatch) -> None:
    import app.calendar.internal_test_adapter as mod

    spy = _ContextSpy()
    monkeypatch.setattr(mod, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    customer_id = await _make_customer(tenant_id)
    adapter = InternalTestCalendarAdapter(async_session_maker)

    from datetime import UTC, datetime

    await adapter.get_availability(
        tenant_id, date_from=datetime(2030, 1, 7, 9, tzinfo=UTC), date_to=datetime(2030, 1, 7, 10, tzinfo=UTC), duration_minutes=30
    )
    appointment = await adapter.create_event(
        BookingRequest(
            tenant_id=tenant_id,
            customer_id=customer_id,
            title="Test appt",
            service="Inspection",
            location="On-site",
            start_time=datetime(2030, 1, 7, 9, tzinfo=UTC),
            end_time=datetime(2030, 1, 7, 9, 30, tzinfo=UTC),
        )
    )
    await adapter.update_event(tenant_id, appointment.id, notes="updated")

    assert len(spy.calls) == 3
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_internal_test_calendar_adapter_never_leaks_across_tenants() -> None:
    """Cross-tenant-denial proof: tenant A's appointment must never be
    reachable through tenant B's own update_event call."""
    from datetime import UTC, datetime

    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org(tenant_a)
    await _make_org(tenant_b)
    customer_id = await _make_customer(tenant_a)

    adapter = InternalTestCalendarAdapter(async_session_maker)
    appointment = await adapter.create_event(
        BookingRequest(
            tenant_id=tenant_a,
            customer_id=customer_id,
            title="Tenant A appt",
            service="Inspection",
            location="On-site",
            start_time=datetime(2030, 2, 1, 9, tzinfo=UTC),
            end_time=datetime(2030, 2, 1, 9, 30, tzinfo=UTC),
        )
    )

    with pytest.raises(ValueError, match="Appointment not found"):
        await adapter.update_event(tenant_b, appointment.id, notes="should not be reachable")
