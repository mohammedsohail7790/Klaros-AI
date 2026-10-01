"""Phase 17B-2R: real-PostgreSQL behavioral proof that the 1
independently-opened session in app/api/v1/public_quotes.py
(_quote_to_dict_with_items, reached via GET /public/quotes/{quote_id})
now stamps `SET LOCAL app.tenant_id`. tenant_id here always comes from
the verified, signed quote_view token, never a client-supplied value —
see the module's own docstring."""

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.core.config import get_settings
from app.core.security import create_quote_view_token
from app.db.session import async_session_maker, set_tenant_context
from app.models.crm import Customer
from app.models.quote import Quote, QuoteStatus

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)


class _ContextSpy:
    def __init__(self):
        self.calls: list[tuple] = []

    async def __call__(self, session, tenant_id):
        await set_tenant_context(session, tenant_id)
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            readback = await session.scalar(text("select current_setting('app.tenant_id', true)"))
        else:
            readback = None
        self.calls.append((tenant_id, readback))


async def _make_quote(tenant_id: uuid.UUID) -> Quote:
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Public Quote Ctx Customer")
        session.add(customer)
        await session.flush()
        quote = Quote(
            tenant_id=tenant_id, customer_id=customer.id, quote_number=f"PQ-{uuid.uuid4().hex[:8]}",
            status=QuoteStatus.SENT, subtotal=Decimal("100.00"), total=Decimal("100.00"),
        )
        session.add(quote)
        await session.commit()
        await session.refresh(quote)
        return quote


@requires_real_postgres
async def test_view_quote_sets_tenant_context(monkeypatch, client: AsyncClient) -> None:
    import app.api.v1.public_quotes as public_quotes_module

    spy = _ContextSpy()
    monkeypatch.setattr(public_quotes_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    quote = await _make_quote(tenant_id)
    token = create_quote_view_token(quote.id, tenant_id)

    resp = await client.get(f"/api/v1/public/quotes/{quote.id}", params={"token": token})
    assert resp.status_code == 200, resp.text

    assert len(spy.calls) >= 1
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)
