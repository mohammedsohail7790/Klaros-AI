"""Phase 17B-2R: real-PostgreSQL behavioral proof that CollectionService's
own, independently-opened sessions (2 sites) now stamp `SET LOCAL
app.tenant_id`.
"""

import uuid
from datetime import date

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.crm import Customer
from app.models.finance import Invoice, InvoiceStatus
from app.models.organization import Organization
from app.services.collection_service import CollectionService

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


async def _make_org_customer_and_invoice(tenant_id: uuid.UUID) -> uuid.UUID:
    customer_id = uuid.uuid4()
    invoice_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        session.add(Customer(id=customer_id, tenant_id=tenant_id, name="Collection Test Customer"))
        session.add(
            Invoice(
                id=invoice_id, tenant_id=tenant_id, customer_id=customer_id, invoice_number=f"COL-{uuid.uuid4().hex[:8]}",
                status=InvoiceStatus.OVERDUE, issue_date=date(2026, 1, 1), due_date=date(2026, 1, 15),
                subtotal=100, total=100, amount_paid=0, amount_due=100,
            )
        )
        await session.commit()
    return invoice_id


@requires_real_postgres
async def test_schedule_next_action_and_execute_due_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.collection_service as collection_service_module

    monkeypatch.setattr(collection_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    invoice_id = await _make_org_customer_and_invoice(tenant_id)
    service = CollectionService(async_session_maker)

    await service.schedule_next_action(tenant_id, invoice_id, days_overdue=5)
    await service.execute_due_actions(tenant_id)

    assert len(spy.calls) >= 2
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_actions_never_visible_in_tenant_bs_execute_due() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    invoice_a = await _make_org_customer_and_invoice(tenant_a)
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_b, name=f"org-{tenant_b}", slug=f"org-{tenant_b}"))
        await session.commit()

    service = CollectionService(async_session_maker)
    await service.schedule_next_action(tenant_a, invoice_a, days_overdue=5)

    executed_b = await service.execute_due_actions(tenant_b)
    assert executed_b == []
