"""Real-PostgreSQL concurrency verification for
`QuickBooksRefundSyncService.sync_refund_to_quickbooks`
(app/services/quickbooks_refund_sync_service.py). Same audit-found bug
class as invoice sync, payment sync, and Google Calendar sync: the
idempotency check (`Refund.quickbooks_refund_receipt_id`) and the final
write happened in separate sessions with no lock held across the real
external QuickBooks call in between.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.core.config import get_settings
from app.integrations.quickbooks_client import QuickBooksClient
from app.integrations.quickbooks_schemas import QuickBooksCompanyInfo, QuickBooksRefundReceiptResponse
from app.models.crm import Customer
from app.models.finance import Payment, PaymentStatus, Refund, RefundStatus
from app.services.quickbooks_refund_sync_service import QuickBooksRefundSyncService

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)


async def _connect_quickbooks(connection_service, tenant_id: uuid.UUID, monkeypatch) -> None:
    async def _fake_get_company_info(self, *, access_token, realm_id):
        return QuickBooksCompanyInfo(CompanyName="Refund Concurrency Test Co", Id=realm_id)

    monkeypatch.setattr(QuickBooksClient, "get_company_info", _fake_get_company_info)
    await connection_service.connect(
        tenant_id, "quickbooks", {"access_token": "at_valid", "refresh_token": "rt_valid", "realm_id": "realm-refund-1"},
        created_by=None, external_account_id="realm-refund-1", scopes="com.intuit.quickbooks.accounting",
    )


async def _build_completed_refund(tenant_id: uuid.UUID) -> Refund:
    from app.db.session import async_session_maker

    async with async_session_maker() as session:
        customer = Customer(
            tenant_id=tenant_id, name="QB Refund Concurrency Customer",
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
            reason="Concurrency test refund", status=RefundStatus.COMPLETED,
        )
        session.add(refund)
        await session.commit()
        await session.refresh(refund)
        return refund


@requires_real_postgres
async def test_genuinely_concurrent_refund_sync_creates_exactly_one_qb_refund_receipt(monkeypatch) -> None:
    import asyncio

    from app.api.tool_deps_integrations import get_integration_connection_service
    from app.db.session import async_session_maker

    connection_service = get_integration_connection_service()
    tenant_id = uuid.uuid4()
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)
    refund = await _build_completed_refund(tenant_id)

    refund_create_calls: list[str] = []

    async def _fake_create_refund_receipt(self, *, access_token, realm_id, customer_id, payment_id, amount, request_id=None):
        await asyncio.sleep(0.05)
        rid = f"qbrefund-{len(refund_create_calls)}"
        refund_create_calls.append(rid)
        return QuickBooksRefundReceiptResponse(Id=rid)

    monkeypatch.setattr(QuickBooksClient, "create_refund_receipt", _fake_create_refund_receipt)

    service = QuickBooksRefundSyncService(async_session_maker, connection_service)

    results = await asyncio.gather(
        *[service.sync_refund_to_quickbooks(tenant_id, refund.id) for _ in range(10)]
    )

    assert len(refund_create_calls) == 1, f"expected exactly 1 real QB refund receipt create, got {len(refund_create_calls)}: {refund_create_calls}"
    already_synced = [r for r in results if r.already_synced]
    freshly_synced = [r for r in results if not r.already_synced]
    assert len(freshly_synced) == 1
    assert len(already_synced) == 9
    assert {r.quickbooks_refund_receipt_id for r in results} == {refund_create_calls[0]}

    async with async_session_maker() as session:
        refreshed = await session.get(Refund, refund.id)
        assert refreshed.quickbooks_refund_receipt_id == refund_create_calls[0]
