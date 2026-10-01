"""Phase 17B-2R: real-PostgreSQL behavioral proof that QuoteService's own,
independently-opened sessions (8 sites, including `get_for_public_view`,
whose `tenant_id` comes from the documented public quote-view-token URL
boundary per §21) now stamp `SET LOCAL app.tenant_id`.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.events.bus import EventBus
from app.events.transport import InMemoryTransport
from app.models.crm import Customer
from app.models.organization import Organization
from app.services.invoice_service import LineItemInput
from app.services.quote_service import QuoteNotFoundError, QuoteService

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
        session.add(Customer(id=customer_id, tenant_id=tenant_id, name="Quote Test Customer"))
        await session.commit()
    return customer_id


def _service() -> QuoteService:
    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    return QuoteService(async_session_maker, bus)


@requires_real_postgres
async def test_create_draft_and_public_view_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.quote_service as quote_service_module

    monkeypatch.setattr(quote_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    customer_id = await _make_org_and_customer(tenant_id)
    service = _service()

    quote, deduped = await service.create_draft(
        tenant_id, customer_id=customer_id, lead_id=None,
        items=[LineItemInput(description="Consulting", quantity=Decimal("1"), unit_price=Decimal("250.00"))],
    )
    assert not deduped

    await service.get_for_public_view(tenant_id, quote.id)

    assert len(spy.calls) >= 2
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_view_tenant_bs_quote() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    customer_a = await _make_org_and_customer(tenant_a)
    await _make_org_and_customer(tenant_b)
    service = _service()

    quote, _ = await service.create_draft(
        tenant_a, customer_id=customer_a, lead_id=None,
        items=[LineItemInput(description="A-only", quantity=Decimal("1"), unit_price=Decimal("100.00"))],
    )

    with pytest.raises(QuoteNotFoundError):
        await service.get_for_public_view(tenant_b, quote.id)
