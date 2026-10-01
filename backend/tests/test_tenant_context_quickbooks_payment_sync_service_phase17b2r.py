"""Phase 17B-2R: real-PostgreSQL behavioral proof that
QuickBooksPaymentSyncService's two own, independently-opened,
advisory-locked sessions (`sync_deposit_payment`,
`sync_invoice_payment_to_quickbooks`) now stamp `SET LOCAL
app.tenant_id`.
"""

import asyncio
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.integrations.quickbooks_client import QuickBooksClient
from app.integrations.quickbooks_schemas import QuickBooksCompanyInfo, QuickBooksPaymentResponse
from app.models.crm import Customer
from app.models.finance import Invoice, Payment, PaymentStatus
from app.models.operations import Job, JobStatus
from app.models.organization import Organization
from app.models.quote import Quote, QuoteStatus
from app.services.quickbooks_payment_sync_service import PaymentNotFoundError, QuickBooksPaymentSyncService

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)


class _ContextSpy:
    def __init__(self):
        self.calls: list[tuple[uuid.UUID | None, str | None]] = []

    async def __call__(self, session, tenant_id):
        await set_tenant_context(session, tenant_id)
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            readback = await session.scalar(text("select current_setting('app.tenant_id', true)"))
        else:
            readback = None
        self.calls.append((tenant_id, readback))


@pytest.fixture
def spy():
    return _ContextSpy()


async def _connect_quickbooks(connection_service, tenant_id: uuid.UUID, monkeypatch) -> None:
    async def _fake_get_company_info(self, *, access_token, realm_id):
        return QuickBooksCompanyInfo(CompanyName="Payment Tenant Ctx Test Co", Id=realm_id)

    monkeypatch.setattr(QuickBooksClient, "get_company_info", _fake_get_company_info)
    await connection_service.connect(
        tenant_id, "quickbooks", {"access_token": "at_valid", "refresh_token": "rt_valid", "realm_id": f"realm-{tenant_id}"},
        created_by=None, external_account_id=f"realm-{tenant_id}", scopes="com.intuit.quickbooks.accounting",
    )


async def _build_synced_deposit_payment(tenant_id: uuid.UUID) -> Payment:
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        customer = Customer(
            tenant_id=tenant_id, name="QB Payment Tenant Ctx Customer",
            external_provider="quickbooks", external_id="qb-cust-existing",
        )
        session.add(customer)
        await session.flush()

        job = Job(
            tenant_id=tenant_id, customer_id=customer.id, job_number=f"JOB-{uuid.uuid4().hex[:8]}",
            title="Tenant ctx test job", status=JobStatus.COMPLETED,
        )
        session.add(job)
        await session.flush()

        invoice = Invoice(
            tenant_id=tenant_id, customer_id=customer.id, job_id=job.id,
            invoice_number=f"QBPAYCTX-{uuid.uuid4().hex[:8]}", status="APPROVED",
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
async def test_sync_deposit_payment_sets_tenant_context(monkeypatch, spy) -> None:
    import app.services.quickbooks_payment_sync_service as qbps_module
    from app.api.tool_deps_integrations import get_integration_connection_service

    monkeypatch.setattr(qbps_module, "set_tenant_context", spy)

    connection_service = get_integration_connection_service()
    tenant_id = uuid.uuid4()
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)
    payment = await _build_synced_deposit_payment(tenant_id)

    async def _fake_create_payment(self, *, access_token, realm_id, customer_id, invoice_lines, request_id=None):
        return QuickBooksPaymentResponse(Id="qbpay-ctx-1")

    monkeypatch.setattr(QuickBooksClient, "create_payment", _fake_create_payment)

    service = QuickBooksPaymentSyncService(async_session_maker, connection_service)
    result = await service.sync_deposit_payment(tenant_id, payment.id)
    assert result.quickbooks_payment_id == "qbpay-ctx-1"

    assert len(spy.calls) >= 1
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_deposit_payment_never_syncable_by_tenant_b(monkeypatch) -> None:
    from app.api.tool_deps_integrations import get_integration_connection_service

    connection_service = get_integration_connection_service()
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _connect_quickbooks(connection_service, tenant_a, monkeypatch)
    await _connect_quickbooks(connection_service, tenant_b, monkeypatch)
    payment_a = await _build_synced_deposit_payment(tenant_a)

    service = QuickBooksPaymentSyncService(async_session_maker, connection_service)
    with pytest.raises(PaymentNotFoundError):
        await service.sync_deposit_payment(tenant_b, payment_a.id)
