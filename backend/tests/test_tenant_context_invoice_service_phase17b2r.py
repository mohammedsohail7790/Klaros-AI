"""Phase 17B-2R: real-PostgreSQL behavioral proof that InvoiceService's own,
independently-opened sessions (`self._session_factory()`, 8 sites) now
stamp `SET LOCAL app.tenant_id`. Same methodology as
`test_tenant_context_automation_service_phase17b2r.py`. Special attention
per task §13: this test never touches Stripe webhook signature
verification or trusts a client-supplied tenant_id — tenant identity here
always comes from the caller-trusted `tenant_id` parameter, exactly as the
production call sites (API routers reading it from CurrentUser, event
handlers reading it from the event row) already do.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.events.factory import get_event_bus
from app.models.crm import Customer
from app.models.organization import Organization
from app.services.invoice_service import InvoiceNotFoundError, InvoiceService, LineItemInput

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


async def _make_org_and_customer(tenant_id: uuid.UUID) -> uuid.UUID:
    customer_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        session.add(Customer(id=customer_id, tenant_id=tenant_id, name="Test Customer"))
        await session.commit()
    return customer_id


def _service() -> InvoiceService:
    return InvoiceService(async_session_maker, get_event_bus())


@requires_real_postgres
async def test_create_manual_draft_and_request_approval_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.invoice_service as invoice_service_module

    monkeypatch.setattr(invoice_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    customer_id = await _make_org_and_customer(tenant_id)
    service = _service()

    invoice = await service.create_manual_draft(
        tenant_id, customer_id=customer_id, job_id=None,
        items=[LineItemInput(description="Consulting", quantity=Decimal("1"), unit_price=Decimal("500.00"))],
    )
    await service.request_approval(tenant_id, invoice.id, actor_id=None)

    assert len(spy.calls) >= 2
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_void_tenant_bs_invoice() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    customer_a = await _make_org_and_customer(tenant_a)
    await _make_org_and_customer(tenant_b)
    service = _service()

    invoice = await service.create_manual_draft(
        tenant_a, customer_id=customer_a, job_id=None,
        items=[LineItemInput(description="A-only", quantity=Decimal("1"), unit_price=Decimal("100.00"))],
    )

    with pytest.raises(InvoiceNotFoundError):
        await service.void_invoice(tenant_b, invoice.id, reason="cross-tenant probe")


@requires_real_postgres
async def test_pool_reuse_a_b_a_b_never_leaks_invoice_tenant_context() -> None:
    """§17 A/B/A/B pool-safety proof on a small pool, specific to
    InvoiceService's own session-open sites."""
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    customer_a = await _make_org_and_customer(tenant_a)
    customer_b = await _make_org_and_customer(tenant_b)
    service = _service()

    for _ in range(2):
        inv_a = await service.create_manual_draft(
            tenant_a, customer_id=customer_a, job_id=None,
            items=[LineItemInput(description="a", quantity=Decimal("1"), unit_price=Decimal("10.00"))],
        )
        inv_b = await service.create_manual_draft(
            tenant_b, customer_id=customer_b, job_id=None,
            items=[LineItemInput(description="b", quantity=Decimal("1"), unit_price=Decimal("10.00"))],
        )
        # Each tenant can read only its own invoice back.
        with pytest.raises(InvoiceNotFoundError):
            await service.void_invoice(tenant_b, inv_a.id, reason="cross-check")
        with pytest.raises(InvoiceNotFoundError):
            await service.void_invoice(tenant_a, inv_b.id, reason="cross-check")
