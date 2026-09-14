"""The QuickBooks PULL direction (finance.import_from_quickbooks /
QuickBooksImportService) — the counterpart to test_quickbooks_integration.py's
push-direction tests. Mocks QuickBooksClient.query_customers/query_invoices
directly (same pattern test_quickbooks_integration.py uses for
create_customer/create_invoice), no real Intuit credentials needed.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.integrations.quickbooks_client import QuickBooksAPIError, QuickBooksClient, QuickBooksErrorType
from app.integrations.quickbooks_schemas import (
    QuickBooksCustomerQueryRow,
    QuickBooksCustomerRef,
    QuickBooksEmailAddr,
    QuickBooksInvoiceQueryRow,
)
from app.models.crm import Customer
from app.models.finance import Invoice, InvoiceStatus
from app.services.integration_connection_service import IntegrationConnectionService
from app.services.invoice_service import InvoiceService
from app.services.quickbooks_import_service import QuickBooksImportService
from app.services.quickbooks_sync_service import QuickBooksNotConnectedError

pytestmark = pytest.mark.asyncio


@pytest.fixture
def connection_service() -> IntegrationConnectionService:
    from app.api.tool_deps_integrations import get_integration_connection_service

    return get_integration_connection_service()


async def _connect_quickbooks(connection_service, tenant_id: uuid.UUID, monkeypatch) -> None:
    from app.integrations.quickbooks_schemas import QuickBooksCompanyInfo

    async def _fake_get_company_info(self, *, access_token, realm_id):
        return QuickBooksCompanyInfo(CompanyName="Acme Co", Id=realm_id)

    monkeypatch.setattr(QuickBooksClient, "get_company_info", _fake_get_company_info)
    await connection_service.connect(
        tenant_id, "quickbooks", {"access_token": "at_valid", "refresh_token": "rt_valid", "realm_id": "realm-1"},
        created_by=None, external_account_id="realm-1", scopes="com.intuit.quickbooks.accounting",
    )


def _import_service(connection_service) -> QuickBooksImportService:
    from app.db.session import async_session_maker
    from app.events.bus import EventBus
    from app.events.transport import InMemoryTransport

    invoice_service = InvoiceService(
        async_session_maker, EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    )
    return QuickBooksImportService(async_session_maker, connection_service, invoice_service)


def _paged(rows):
    """QuickBooksImportService pages in batches of 100 — a fake that
    returns `rows` on the first page and an empty list after, regardless
    of how many pages get requested."""
    calls = {"n": 0}

    async def _fake(self, *, access_token, realm_id, start_position, max_results):
        calls["n"] += 1
        return rows if start_position == 1 else []

    return _fake, calls


async def test_no_connection_raises(connection_service) -> None:
    service = _import_service(connection_service)
    with pytest.raises(QuickBooksNotConnectedError):
        await service.import_customers(uuid.uuid4())


async def test_pull_creates_new_customer(connection_service, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)

    rows = [
        QuickBooksCustomerQueryRow(
            Id="qbo-cust-1", DisplayName="Pulled Customer",
            PrimaryEmailAddr=QuickBooksEmailAddr(Address="pulled@example.com"),
        )
    ]
    fake, _ = _paged(rows)
    monkeypatch.setattr(QuickBooksClient, "query_customers", fake)

    service = _import_service(connection_service)
    created, matched = await service.import_customers(tenant_id)
    assert created == 1
    assert matched == 0

    from app.db.session import async_session_maker
    async with async_session_maker() as session:
        from sqlalchemy import select

        customer = (
            await session.execute(select(Customer).where(Customer.tenant_id == tenant_id))
        ).scalar_one()
    assert customer.name == "Pulled Customer"
    assert customer.external_provider == "quickbooks"
    assert customer.external_id == "qbo-cust-1"


async def test_pull_links_instead_of_duplicating_an_existing_customer(connection_service, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)

    from app.db.session import async_session_maker
    async with async_session_maker() as session:
        existing = Customer(tenant_id=tenant_id, name="Already In Klaros", email="shared@example.com")
        session.add(existing)
        await session.commit()
        await session.refresh(existing)

    rows = [
        QuickBooksCustomerQueryRow(
            Id="qbo-cust-2", DisplayName="Same Person Different Name",
            PrimaryEmailAddr=QuickBooksEmailAddr(Address="SHARED@example.com"),
        )
    ]
    fake, _ = _paged(rows)
    monkeypatch.setattr(QuickBooksClient, "query_customers", fake)

    service = _import_service(connection_service)
    created, matched = await service.import_customers(tenant_id)
    assert created == 0
    assert matched == 1

    async with async_session_maker() as session:
        refreshed = await session.get(Customer, existing.id)
    assert refreshed.external_provider == "quickbooks"
    assert refreshed.external_id == "qbo-cust-2"


async def test_repeat_pull_skips_already_linked_customer(connection_service, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)

    rows = [QuickBooksCustomerQueryRow(Id="qbo-cust-3", DisplayName="Repeat Test")]
    fake, calls = _paged(rows)
    monkeypatch.setattr(QuickBooksClient, "query_customers", fake)

    service = _import_service(connection_service)
    first = await service.import_customers(tenant_id)
    second = await service.import_customers(tenant_id)
    assert first == (1, 0)
    assert second == (0, 0)  # already linked, not re-created or re-matched


async def test_invoice_pulls_as_sent_when_fully_unpaid(connection_service, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)

    customer_rows = [
        QuickBooksCustomerQueryRow(Id="qbo-cust-4", DisplayName="Invoice Customer", PrimaryEmailAddr=QuickBooksEmailAddr(Address="invcust@example.com"))
    ]
    invoice_rows = [
        QuickBooksInvoiceQueryRow(
            Id="qbo-inv-1", DocNumber="QB-1001", TotalAmt=500.0, Balance=500.0,
            TxnDate="2026-08-01", DueDate="2026-09-01",
            CustomerRef=QuickBooksCustomerRef(value="qbo-cust-4", name="Invoice Customer"),
        )
    ]
    fake_customers, _ = _paged(customer_rows)
    fake_invoices, _ = _paged(invoice_rows)
    monkeypatch.setattr(QuickBooksClient, "query_customers", fake_customers)
    monkeypatch.setattr(QuickBooksClient, "query_invoices", fake_invoices)

    service = _import_service(connection_service)
    result = await service.import_all(tenant_id)
    assert result.customers_created == 1
    assert result.invoices_created == 1

    from app.db.session import async_session_maker
    async with async_session_maker() as session:
        from sqlalchemy import select

        invoice = (await session.execute(select(Invoice).where(Invoice.tenant_id == tenant_id))).scalar_one()
    assert invoice.status == InvoiceStatus.SENT
    assert invoice.total == Decimal("500.0")
    assert invoice.amount_due == Decimal("500.0")
    assert invoice.external_provider == "quickbooks"
    assert invoice.external_id == "qbo-inv-1"
    assert invoice.invoice_number == "QB-1001"


async def test_invoice_pulls_as_paid_when_balance_zero(connection_service, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)

    customer_rows = [QuickBooksCustomerQueryRow(Id="qbo-cust-5", DisplayName="Paid Customer")]
    invoice_rows = [
        QuickBooksInvoiceQueryRow(
            Id="qbo-inv-2", DocNumber="QB-1002", TotalAmt=300.0, Balance=0.0,
            TxnDate="2026-07-01", DueDate="2026-07-31",
            CustomerRef=QuickBooksCustomerRef(value="qbo-cust-5", name="Paid Customer"),
        )
    ]
    fake_customers, _ = _paged(customer_rows)
    fake_invoices, _ = _paged(invoice_rows)
    monkeypatch.setattr(QuickBooksClient, "query_customers", fake_customers)
    monkeypatch.setattr(QuickBooksClient, "query_invoices", fake_invoices)

    service = _import_service(connection_service)
    result = await service.import_all(tenant_id)

    from app.db.session import async_session_maker
    async with async_session_maker() as session:
        from sqlalchemy import select

        invoice = (await session.execute(select(Invoice).where(Invoice.tenant_id == tenant_id))).scalar_one()
    assert invoice.status == InvoiceStatus.PAID
    assert invoice.amount_due == Decimal("0")


async def test_invoice_with_colliding_number_is_skipped_not_fatal(connection_service, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)

    from app.db.session import async_session_maker
    async with async_session_maker() as session:
        existing_customer = Customer(tenant_id=tenant_id, name="Pre-existing")
        session.add(existing_customer)
        await session.flush()
        session.add(
            Invoice(
                tenant_id=tenant_id, customer_id=existing_customer.id, invoice_number="QB-DUP",
                status=InvoiceStatus.SENT, issue_date=date(2026, 1, 1), due_date=date(2026, 2, 1),
                subtotal=Decimal("10"), total=Decimal("10"), amount_due=Decimal("10"),
            )
        )
        await session.commit()

    invoice_rows = [
        QuickBooksInvoiceQueryRow(
            Id="qbo-inv-3", DocNumber="QB-DUP", TotalAmt=999.0, Balance=999.0,
            CustomerRef=QuickBooksCustomerRef(value=None, name="Some New Name"),
        )
    ]
    fake_customers, _ = _paged([])
    fake_invoices, _ = _paged(invoice_rows)
    monkeypatch.setattr(QuickBooksClient, "query_customers", fake_customers)
    monkeypatch.setattr(QuickBooksClient, "query_invoices", fake_invoices)

    service = _import_service(connection_service)
    result = await service.import_all(tenant_id)
    assert result.invoices_created == 0
    assert result.invoices_skipped == 1
    assert result.invoice_results[0].status == "skipped"


async def test_401_during_customer_pull_refreshes_token_once_and_retries(connection_service, monkeypatch) -> None:
    from app.integrations.quickbooks_schemas import QuickBooksTokenResponse

    tenant_id = uuid.uuid4()
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)

    calls = {"n": 0}

    async def _fake_query_customers(self, *, access_token, realm_id, start_position, max_results):
        calls["n"] += 1
        if calls["n"] == 1:
            raise QuickBooksAPIError("Token expired", error_type=QuickBooksErrorType.AUTHENTICATION)
        assert access_token == "at_refreshed"
        return [] if start_position > 1 else [QuickBooksCustomerQueryRow(Id="qbo-cust-6", DisplayName="After Refresh")]

    async def _fake_refresh(self, *, refresh_token):
        return QuickBooksTokenResponse(access_token="at_refreshed", refresh_token="rt_refreshed", expires_in=3600)

    monkeypatch.setattr(QuickBooksClient, "query_customers", _fake_query_customers)
    monkeypatch.setattr(QuickBooksClient, "refresh_access_token", _fake_refresh)

    service = _import_service(connection_service)
    created, matched = await service.import_customers(tenant_id)
    assert created == 1
    assert calls["n"] == 2
