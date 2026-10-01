"""Phase 17B-2R: real-PostgreSQL behavioral proof that the 1
independently-opened session in app/tools/builtin/quote_tools.py
(GetQuote) now stamps `SET LOCAL app.tenant_id`."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.actor import ActorType
from app.models.crm import Customer
from app.models.quote import Quote, QuoteStatus
from app.models.rbac import Role
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)


def _ctx(tenant_id, role=Role.OWNER, actor_type=ActorType.USER):
    return ExecutionContext(tenant_id=tenant_id, actor_type=actor_type, actor_id=uuid.uuid4(), role=role)


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


async def _make_quote(tenant_id: uuid.UUID) -> Quote:
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Quote Tools Test Customer")
        session.add(customer)
        await session.flush()
        quote = Quote(
            tenant_id=tenant_id, customer_id=customer.id, quote_number=f"QT-{uuid.uuid4().hex[:8]}",
            status=QuoteStatus.DRAFT, subtotal=Decimal("100.00"), total=Decimal("100.00"),
        )
        session.add(quote)
        await session.commit()
        await session.refresh(quote)
        return quote


@requires_real_postgres
async def test_get_quote_sets_tenant_context(monkeypatch, spy, tool_registry) -> None:
    import app.tools.builtin.quote_tools as quote_tools_module

    monkeypatch.setattr(quote_tools_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    quote = await _make_quote(tenant_id)
    ctx = _ctx(tenant_id)

    result = await tool_registry.execute("quotes.get_quote", {"quote_id": str(quote.id)}, ctx)
    assert result.quote["id"] == str(quote.id)

    assert len(spy.calls) >= 1
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_quote_never_fetchable_by_tenant_b(tool_registry) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    quote_a = await _make_quote(tenant_a)

    with pytest.raises(ValueError, match="not found"):
        await tool_registry.execute("quotes.get_quote", {"quote_id": str(quote_a.id)}, _ctx(tenant_b))
