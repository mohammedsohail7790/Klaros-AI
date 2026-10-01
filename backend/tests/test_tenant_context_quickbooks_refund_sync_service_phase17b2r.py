"""Phase 17B-2R: real-PostgreSQL behavioral proof that
QuickBooksRefundSyncService's own, independently-opened,
advisory-locked session (1 site, `sync_refund_to_quickbooks`) now
stamps `SET LOCAL app.tenant_id`.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.integrations.quickbooks_client import QuickBooksClient
from app.integrations.quickbooks_schemas import QuickBooksCompanyInfo, QuickBooksRefundReceiptResponse
from app.models.crm import Customer
from app.models.finance import Payment, PaymentStatus, Refund, RefundStatus
from app.models.organization import Organization
from app.services.quickbooks_refund_sync_service import QuickBooksRefundSyncService, RefundNotFoundError

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
        return QuickBooksCompanyInfo(CompanyName="Refund Tenant Ctx Test Co", Id=realm_id)

    monkeypatch.setattr(QuickBooksClient, "get_company_info", _fake_get_company_info)
    await connection_service.connect(
        tenant_id, "quickbooks", {"access_token": "at_valid", "refresh_token": "rt_valid", "realm_id": f"realm-{tenant_id}"},
        created_by=None, external_account_id=f"realm-{tenant_id}", scopes="com.intuit.quickbooks.accounting",
    )


async def _build_completed_refund(tenant_id: uuid.UUID) -> Refund:
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        customer = Customer(
            tenant_id=tenant_id, name="QB Refund Tenant Ctx Customer",
            external_provider="quickbooks", external_id="qb-cust-existing",
        )
        session.add(customer)
        await session.flush()

        payment = Payment(
            tenant_id=tenant_id, customer_id=customer.id,
            amount=Decimal("400.00"), provider="stripe", external_id=f"pi_{uuid.uuid4().hex}",
            payment_method="card", status=PaymentStatus.REFUNDED,
            received_at=datetime.now(timezone.utc), quickbooks_payment_id="qb-pay-existing",
        )
        session.add(payment)
        await session.flush()

        refund = Refund(
            tenant_id=tenant_id, payment_id=payment.id, amount=Decimal("400.00"),
            reason="Tenant ctx test refund", status=RefundStatus.COMPLETED,
        )
        session.add(refund)
        await session.commit()
        await session.refresh(refund)
        return refund


@requires_real_postgres
async def test_sync_refund_sets_tenant_context(monkeypatch, spy) -> None:
    import app.services.quickbooks_refund_sync_service as qbrs_module
    from app.api.tool_deps_integrations import get_integration_connection_service

    monkeypatch.setattr(qbrs_module, "set_tenant_context", spy)

    connection_service = get_integration_connection_service()
    tenant_id = uuid.uuid4()
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)
    refund = await _build_completed_refund(tenant_id)

    async def _fake_create_refund_receipt(self, *, access_token, realm_id, customer_id, payment_id, amount, request_id=None):
        return QuickBooksRefundReceiptResponse(Id="qbrefund-ctx-1")

    monkeypatch.setattr(QuickBooksClient, "create_refund_receipt", _fake_create_refund_receipt)

    service = QuickBooksRefundSyncService(async_session_maker, connection_service)
    result = await service.sync_refund_to_quickbooks(tenant_id, refund.id)
    assert result.quickbooks_refund_receipt_id == "qbrefund-ctx-1"

    assert len(spy.calls) >= 1
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_refund_never_syncable_by_tenant_b(monkeypatch) -> None:
    from app.api.tool_deps_integrations import get_integration_connection_service

    connection_service = get_integration_connection_service()
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _connect_quickbooks(connection_service, tenant_a, monkeypatch)
    await _connect_quickbooks(connection_service, tenant_b, monkeypatch)
    refund_a = await _build_completed_refund(tenant_a)

    service = QuickBooksRefundSyncService(async_session_maker, connection_service)
    with pytest.raises(RefundNotFoundError):
        await service.sync_refund_to_quickbooks(tenant_b, refund_a.id)
