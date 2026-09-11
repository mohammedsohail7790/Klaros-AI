"""Real-PostgreSQL concurrency verification for
`QuickBooksSyncService.sync_invoice` (app/services/quickbooks_sync_service.py).
The audit that preceded this fix found this method shared the exact same
"read not-yet-synced state in one session, make the real external
provider call, write the result back in a later session with no lock
held" shape that caused a real, confirmed defect in
`GoogleCalendarSyncService.sync_appointment` (10 concurrent syncs of one
appointment created 10 real, distinct Google events). This file exists
to observe real behavior under genuine concurrent PostgreSQL
transactions for the QuickBooks invoice-sync path specifically, not to
assume either safety or a defect.
"""

import asyncio
import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.core.config import get_settings
from app.integrations.quickbooks_client import QuickBooksClient
from app.integrations.quickbooks_schemas import QuickBooksCustomerResponse, QuickBooksInvoiceResponse
from app.models.crm import Customer
from app.models.finance import Invoice, InvoiceLineItem, InvoiceStatus
from app.services.quickbooks_sync_service import QuickBooksSyncService

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)


async def _make_customer_and_invoice(tenant_id: uuid.UUID) -> tuple[Customer, Invoice]:
    from app.db.session import async_session_maker

    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="QB Concurrency Test Customer", email="c@example.com")
        session.add(customer)
        await session.flush()
        invoice = Invoice(
            tenant_id=tenant_id, customer_id=customer.id, invoice_number=f"QBPG-{uuid.uuid4().hex[:8]}",
            status=InvoiceStatus.APPROVED, issue_date=date(2026, 1, 1), due_date=date(2026, 2, 1),
            subtotal=Decimal("150.00"), total=Decimal("150.00"), amount_due=Decimal("150.00"),
        )
        session.add(invoice)
        await session.flush()
        session.add(InvoiceLineItem(
            tenant_id=tenant_id, invoice_id=invoice.id, description="Service call",
            quantity=Decimal("1"), unit_price=Decimal("150.00"), line_total=Decimal("150.00"),
        ))
        await session.commit()
        await session.refresh(customer)
        await session.refresh(invoice)
    return customer, invoice


async def _connect_quickbooks(connection_service, tenant_id: uuid.UUID, monkeypatch) -> None:
    from app.integrations.quickbooks_schemas import QuickBooksCompanyInfo

    async def _fake_get_company_info(self, *, access_token, realm_id):
        return QuickBooksCompanyInfo(CompanyName="Concurrency Test Co", Id=realm_id)

    monkeypatch.setattr(QuickBooksClient, "get_company_info", _fake_get_company_info)
    await connection_service.connect(
        tenant_id, "quickbooks", {"access_token": "at_valid", "refresh_token": "rt_valid", "realm_id": "realm-concurrency-1"},
        created_by=None, external_account_id="realm-concurrency-1", scopes="com.intuit.quickbooks.accounting",
    )


@requires_real_postgres
async def test_genuinely_concurrent_sync_of_the_same_invoice_creates_exactly_one_qb_invoice(monkeypatch) -> None:
    from app.api.tool_deps_integrations import get_integration_connection_service
    from app.db.session import async_session_maker

    connection_service = get_integration_connection_service()
    tenant_id = uuid.uuid4()
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)
    _customer, invoice = await _make_customer_and_invoice(tenant_id)

    customer_create_calls: list[str] = []
    invoice_create_calls: list[str] = []

    async def _fake_create_customer(self, *, access_token, realm_id, display_name, email, phone):
        await asyncio.sleep(0.05)  # a real external call is never instant — widens the race window
        cid = f"qbcust-{len(customer_create_calls)}"
        customer_create_calls.append(cid)
        return QuickBooksCustomerResponse(Id=cid, DisplayName=display_name)

    async def _fake_create_invoice(self, *, access_token, realm_id, customer_id, doc_number, lines):
        await asyncio.sleep(0.05)
        iid = f"qbinv-{len(invoice_create_calls)}"
        invoice_create_calls.append(iid)
        return QuickBooksInvoiceResponse(Id=iid, DocNumber=doc_number)

    monkeypatch.setattr(QuickBooksClient, "create_customer", _fake_create_customer)
    monkeypatch.setattr(QuickBooksClient, "create_invoice", _fake_create_invoice)

    service = QuickBooksSyncService(async_session_maker, connection_service)

    results = await asyncio.gather(
        *[service.sync_invoice(tenant_id, invoice.id) for _ in range(10)]
    )

    assert len(customer_create_calls) == 1, f"expected exactly 1 real QB customer create, got {len(customer_create_calls)}: {customer_create_calls}"
    assert len(invoice_create_calls) == 1, f"expected exactly 1 real QB invoice create, got {len(invoice_create_calls)}: {invoice_create_calls}"

    already_synced = [r for r in results if r.already_synced]
    freshly_synced = [r for r in results if not r.already_synced]
    assert len(freshly_synced) == 1, f"expected exactly 1 fresh sync, got {len(freshly_synced)}"
    assert len(already_synced) == 9, f"expected exactly 9 already_synced results, got {len(already_synced)}"
    assert {r.quickbooks_invoice_id for r in results} == {invoice_create_calls[0]}

    async with async_session_maker() as session:
        refreshed_invoice = await session.get(Invoice, invoice.id)
        assert refreshed_invoice.external_provider == "quickbooks"
        assert refreshed_invoice.external_id == invoice_create_calls[0]

        refreshed_customer = await session.get(Customer, _customer.id)
        assert refreshed_customer.external_provider == "quickbooks"
        assert refreshed_customer.external_id == customer_create_calls[0]
