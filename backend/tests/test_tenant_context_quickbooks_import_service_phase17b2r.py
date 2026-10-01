"""Phase 17B-2R: real-PostgreSQL behavioral proof that
QuickBooksImportService's own, independently-opened sessions (3 sites)
now stamp `SET LOCAL app.tenant_id`. Same methodology as
`test_tenant_context_automation_service_phase17b2r.py`.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.events.bus import EventBus
from app.events.transport import InMemoryTransport
from app.integrations.quickbooks_client import QuickBooksClient
from app.integrations.quickbooks_schemas import QuickBooksCustomerQueryRow, QuickBooksEmailAddr
from app.models.organization import Organization
from app.services.integration_connection_service import IntegrationConnectionService
from app.services.invoice_service import InvoiceService
from app.services.quickbooks_import_service import QuickBooksImportService

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


@pytest.fixture
def connection_service():
    from app.api.tool_deps_integrations import get_integration_connection_service

    return get_integration_connection_service()


async def _make_org(tenant_id: uuid.UUID) -> None:
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        await session.commit()


async def _connect_quickbooks(connection_service, tenant_id: uuid.UUID, monkeypatch) -> None:
    from app.integrations.quickbooks_schemas import QuickBooksCompanyInfo

    async def _fake_get_company_info(self, *, access_token, realm_id):
        return QuickBooksCompanyInfo(CompanyName="Test Co", Id=realm_id)

    monkeypatch.setattr(QuickBooksClient, "get_company_info", _fake_get_company_info)
    await connection_service.connect(
        tenant_id, "quickbooks", {"access_token": "at_valid", "refresh_token": "rt_valid", "realm_id": "realm-1"},
        created_by=None, external_account_id="realm-1", scopes="com.intuit.quickbooks.accounting",
    )


def _import_service(connection_service) -> QuickBooksImportService:
    invoice_service = InvoiceService(
        async_session_maker, EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    )
    return QuickBooksImportService(async_session_maker, connection_service, invoice_service)


@requires_real_postgres
async def test_import_customers_sets_tenant_context(monkeypatch, spy, connection_service) -> None:
    import app.services.quickbooks_import_service as qbi_module

    monkeypatch.setattr(qbi_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)

    rows = [
        QuickBooksCustomerQueryRow(
            Id="qbo-cust-phase17b2r", DisplayName="Phase17b2r Customer",
            PrimaryEmailAddr=QuickBooksEmailAddr(Address="phase17b2r@example.com"),
        )
    ]

    async def _fake(self, *, access_token, realm_id, start_position, max_results):
        return rows if start_position == 1 else []

    monkeypatch.setattr(QuickBooksClient, "query_customers", _fake)

    service = _import_service(connection_service)
    created, matched = await service.import_customers(tenant_id)
    assert created == 1

    assert len(spy.calls) >= 1
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_import_never_matches_tenant_bs_customer(monkeypatch, connection_service) -> None:
    """Two tenants importing a QuickBooks customer with the SAME external
    id must each get their own, separate Customer row — never
    cross-matched."""
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org(tenant_a)
    await _make_org(tenant_b)
    await _connect_quickbooks(connection_service, tenant_a, monkeypatch)
    await _connect_quickbooks(connection_service, tenant_b, monkeypatch)

    rows = [
        QuickBooksCustomerQueryRow(
            Id="qbo-shared-id", DisplayName="Shared External Id Customer", PrimaryEmailAddr=None,
        )
    ]

    async def _fake(self, *, access_token, realm_id, start_position, max_results):
        return rows if start_position == 1 else []

    monkeypatch.setattr(QuickBooksClient, "query_customers", _fake)

    service = _import_service(connection_service)
    created_a, _ = await service.import_customers(tenant_a)
    created_b, _ = await service.import_customers(tenant_b)
    assert created_a == 1
    assert created_b == 1

    from app.models.crm import Customer
    from sqlalchemy import select

    async with async_session_maker() as session:
        rows_a = (await session.execute(select(Customer).where(Customer.tenant_id == tenant_a))).scalars().all()
        rows_b = (await session.execute(select(Customer).where(Customer.tenant_id == tenant_b))).scalars().all()
    assert len(rows_a) == 1
    assert len(rows_b) == 1
    assert rows_a[0].id != rows_b[0].id
