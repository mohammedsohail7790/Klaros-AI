"""Real-PostgreSQL concurrency verification for
`QuickBooksPaymentSyncService.sync_deposit_payment` /
`sync_invoice_payment_to_quickbooks`
(app/services/quickbooks_payment_sync_service.py). Same audit-found bug
class as `test_postgres_quickbooks_sync_concurrency.py` (invoice sync)
and `test_postgres_google_calendar_sync_concurrency.py` (Google
Calendar): the idempotency check (`Payment.quickbooks_payment_id`) and
the final write happened in separate sessions with no lock held across
the real external QuickBooks call in between. This file proves the fix
(the same `pg_advisory_xact_lock` pattern) for the deposit-payment path
specifically, against real concurrent PostgreSQL transactions.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.core.config import get_settings
from app.integrations.quickbooks_client import QuickBooksClient
from app.integrations.quickbooks_schemas import QuickBooksCompanyInfo, QuickBooksPaymentResponse
from app.models.crm import Customer
from app.models.finance import Invoice, Payment, PaymentStatus
from app.models.operations import Job, JobStatus
from app.models.quote import Quote, QuoteStatus
from app.services.quickbooks_payment_sync_service import QuickBooksPaymentSyncService

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)


async def _connect_quickbooks(connection_service, tenant_id: uuid.UUID, monkeypatch) -> None:
    async def _fake_get_company_info(self, *, access_token, realm_id):
        return QuickBooksCompanyInfo(CompanyName="Payment Concurrency Test Co", Id=realm_id)

    monkeypatch.setattr(QuickBooksClient, "get_company_info", _fake_get_company_info)
    await connection_service.connect(
        tenant_id, "quickbooks", {"access_token": "at_valid", "refresh_token": "rt_valid", "realm_id": "realm-pay-1"},
        created_by=None, external_account_id="realm-pay-1", scopes="com.intuit.quickbooks.accounting",
    )


async def _build_synced_deposit_payment(tenant_id: uuid.UUID) -> Payment:
    """Directly constructs the real row shape sync_deposit_payment
    requires (Payment -> Quote -> Job -> already-QuickBooks-synced
    Invoice + Customer) — a lower-level but equally real setup than
    driving the full quote/booking flow, matching this file's narrow
    concurrency-proof purpose."""
    from app.db.session import async_session_maker

    async with async_session_maker() as session:
        customer = Customer(
            tenant_id=tenant_id, name="QB Payment Concurrency Customer",
            external_provider="quickbooks", external_id="qb-cust-existing",
        )
        session.add(customer)
        await session.flush()

        job = Job(
            tenant_id=tenant_id, customer_id=customer.id, job_number=f"JOB-{uuid.uuid4().hex[:8]}",
            title="Concurrency test job", status=JobStatus.COMPLETED,
        )
        session.add(job)
        await session.flush()

        invoice = Invoice(
            tenant_id=tenant_id, customer_id=customer.id, job_id=job.id,
            invoice_number=f"QBPAY-{uuid.uuid4().hex[:8]}", status="APPROVED",
            issue_date=date(2026, 1, 1), due_date=date(2026, 2, 1),
            subtotal=Decimal("400.00"), total=Decimal("400.00"), amount_due=Decimal("400.00"),
            external_provider="quickbooks", external_id="qb-inv-existing",
        )
        session.add(invoice)
        await session.flush()

        quote = Quote(
            tenant_id=tenant_id, customer_id=customer.id, job_id=job.id,
            quote_number=f"Q-{uuid.uuid4().hex[:8]}",
            status=QuoteStatus.CONVERTED, subtotal=Decimal("400.00"), total=Decimal("400.00"),
            valid_until=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
        session.add(quote)
        await session.flush()

        payment = Payment(
            tenant_id=tenant_id, customer_id=customer.id, quote_id=quote.id,
            amount=Decimal("400.00"), provider="stripe", external_id=f"pi_{uuid.uuid4().hex}",
            payment_method="card", status=PaymentStatus.SUCCEEDED,
            received_at=datetime.now(timezone.utc),
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)
        return payment


@requires_real_postgres
async def test_genuinely_concurrent_deposit_payment_sync_creates_exactly_one_qb_payment(monkeypatch) -> None:
    from app.api.tool_deps_integrations import get_integration_connection_service
    from app.db.session import async_session_maker

    connection_service = get_integration_connection_service()
    tenant_id = uuid.uuid4()
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)
    payment = await _build_synced_deposit_payment(tenant_id)

    import asyncio

    payment_create_calls: list[str] = []

    async def _fake_create_payment(self, *, access_token, realm_id, customer_id, invoice_lines, request_id=None):
        await asyncio.sleep(0.05)
        pid = f"qbpay-{len(payment_create_calls)}"
        payment_create_calls.append(pid)
        return QuickBooksPaymentResponse(Id=pid)

    monkeypatch.setattr(QuickBooksClient, "create_payment", _fake_create_payment)

    service = QuickBooksPaymentSyncService(async_session_maker, connection_service)

    results = await asyncio.gather(
        *[service.sync_deposit_payment(tenant_id, payment.id) for _ in range(10)]
    )

    assert len(payment_create_calls) == 1, f"expected exactly 1 real QB payment create, got {len(payment_create_calls)}: {payment_create_calls}"
    already_synced = [r for r in results if r.already_synced]
    freshly_synced = [r for r in results if not r.already_synced]
    assert len(freshly_synced) == 1
    assert len(already_synced) == 9
    assert {r.quickbooks_payment_id for r in results} == {payment_create_calls[0]}

    async with async_session_maker() as session:
        refreshed = await session.get(Payment, payment.id)
        assert refreshed.quickbooks_payment_id == payment_create_calls[0]
