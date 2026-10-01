"""Phase 17B-2R: real-PostgreSQL behavioral proof that
QuickBooksSyncService's own, independently-opened, advisory-locked
session (1 site, `sync_invoice`) now stamps `SET LOCAL app.tenant_id`
before the lock/query work happens.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.integrations.quickbooks_client import QuickBooksClient
from app.integrations.quickbooks_schemas import QuickBooksCompanyInfo, QuickBooksCustomerResponse, QuickBooksInvoiceResponse
from app.models.crm import Customer
from app.models.finance import Invoice, InvoiceLineItem, InvoiceStatus
from app.models.organization import Organization
from app.services.quickbooks_sync_service import InvoiceNotSyncableError, QuickBooksSyncService

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


async def _make_org_customer_and_invoice(tenant_id: uuid.UUID) -> Invoice:
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        customer = Customer(tenant_id=tenant_id, name="QB Tenant Ctx Customer", email="qbctx@example.com")
        session.add(customer)
        await session.flush()
        invoice = Invoice(
            tenant_id=tenant_id, customer_id=customer.id, invoice_number=f"QBCTX-{uuid.uuid4().hex[:8]}",
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
        await session.refresh(invoice)
    return invoice


async def _connect_quickbooks(connection_service, tenant_id: uuid.UUID, monkeypatch) -> None:
    async def _fake_get_company_info(self, *, access_token, realm_id):
        return QuickBooksCompanyInfo(CompanyName="Tenant Ctx Test Co", Id=realm_id)

    monkeypatch.setattr(QuickBooksClient, "get_company_info", _fake_get_company_info)
    await connection_service.connect(
        tenant_id, "quickbooks", {"access_token": "at_valid", "refresh_token": "rt_valid", "realm_id": f"realm-{tenant_id}"},
        created_by=None, external_account_id=f"realm-{tenant_id}", scopes="com.intuit.quickbooks.accounting",
    )


@requires_real_postgres
async def test_sync_invoice_sets_tenant_context(monkeypatch, spy) -> None:
    import app.services.quickbooks_sync_service as qbs_module
    from app.api.tool_deps_integrations import get_integration_connection_service

    monkeypatch.setattr(qbs_module, "set_tenant_context", spy)

    connection_service = get_integration_connection_service()
    tenant_id = uuid.uuid4()
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)
    invoice = await _make_org_customer_and_invoice(tenant_id)

    async def _fake_create_customer(self, *, access_token, realm_id, display_name, email, phone):
        return QuickBooksCustomerResponse(Id="qbcust-1", DisplayName=display_name)

    async def _fake_create_invoice(self, *, access_token, realm_id, customer_id, doc_number, lines):
        return QuickBooksInvoiceResponse(Id="qbinv-1", DocNumber=doc_number)

    monkeypatch.setattr(QuickBooksClient, "create_customer", _fake_create_customer)
    monkeypatch.setattr(QuickBooksClient, "create_invoice", _fake_create_invoice)

    service = QuickBooksSyncService(async_session_maker, connection_service)
    result = await service.sync_invoice(tenant_id, invoice.id)
    assert result.quickbooks_invoice_id == "qbinv-1"

    assert len(spy.calls) >= 1
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_invoice_never_syncable_by_tenant_b(monkeypatch) -> None:
    from app.api.tool_deps_integrations import get_integration_connection_service

    connection_service = get_integration_connection_service()
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _connect_quickbooks(connection_service, tenant_a, monkeypatch)
    await _connect_quickbooks(connection_service, tenant_b, monkeypatch)
    invoice_a = await _make_org_customer_and_invoice(tenant_a)

    service = QuickBooksSyncService(async_session_maker, connection_service)
    with pytest.raises(InvoiceNotSyncableError):
        await service.sync_invoice(tenant_b, invoice_a.id)
