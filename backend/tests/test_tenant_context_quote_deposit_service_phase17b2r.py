"""Phase 17B-2R: real-PostgreSQL behavioral proof that QuoteDepositService's
own, independently-opened sessions (2 sites) now stamp `SET LOCAL
app.tenant_id`. Stripe-linked flow — no auth/signature logic touched here,
only `set_tenant_context` added using the already-trusted `tenant_id`
parameter, same care as `payment_service.py`.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.integrations.stripe_client import StripeClient
from app.integrations.stripe_schemas import StripeCheckoutSessionResponse
from app.models.crm import Customer
from app.models.organization import Organization
from app.models.quote import DepositType, Quote, QuoteStatus
from app.services.integration_connection_service import IntegrationConnectionService
from app.services.quote_deposit_service import QuoteDepositService, QuoteNotFoundError

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


async def _make_org_customer_and_deposit_quote(tenant_id: uuid.UUID) -> uuid.UUID:
    customer_id = uuid.uuid4()
    quote_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        session.add(Customer(id=customer_id, tenant_id=tenant_id, name="Deposit Test Customer"))
        session.add(
            Quote(
                id=quote_id, tenant_id=tenant_id, quote_number=f"Q-{uuid.uuid4().hex[:6]}", customer_id=customer_id,
                status=QuoteStatus.DEPOSIT_PENDING, deposit_type=DepositType.FIXED, deposit_value=Decimal("50.00"),
                deposit_amount=Decimal("50.00"), total=Decimal("500.00"),
            )
        )
        await session.commit()
    return quote_id


@requires_real_postgres
async def test_preview_sets_tenant_context(monkeypatch, spy) -> None:
    import app.services.quote_deposit_service as qds_module

    monkeypatch.setattr(qds_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    quote_id = await _make_org_customer_and_deposit_quote(tenant_id)
    service = QuoteDepositService(async_session_maker, IntegrationConnectionService(async_session_maker))

    preview = await service.preview(tenant_id, quote_id)
    assert preview.deposit_required is True

    assert len(spy.calls) >= 1
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_create_checkout_session_sets_tenant_context(monkeypatch, spy) -> None:
    import app.services.quote_deposit_service as qds_module

    monkeypatch.setattr(qds_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    quote_id = await _make_org_customer_and_deposit_quote(tenant_id)
    connection_service = IntegrationConnectionService(async_session_maker)

    async def _fake_create_checkout_session(self, **kwargs):
        return StripeCheckoutSessionResponse(id="cs_test_123", url="https://checkout.stripe.test/cs_test_123")

    async def _fake_resolve_stripe_secret_key(connection_service, tenant_id):
        return "sk_test_fake"

    monkeypatch.setattr(StripeClient, "create_checkout_session", _fake_create_checkout_session)
    monkeypatch.setattr(qds_module, "resolve_stripe_secret_key", _fake_resolve_stripe_secret_key)

    service = QuoteDepositService(async_session_maker, connection_service)
    result = await service.create_deposit_checkout_session(
        tenant_id, quote_id, success_url="https://example.com/ok", cancel_url="https://example.com/cancel",
    )
    assert result.checkout_url

    assert len(spy.calls) >= 1
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_preview_tenant_bs_quote() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    quote_a_id = await _make_org_customer_and_deposit_quote(tenant_a)
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_b, name=f"org-{tenant_b}", slug=f"org-{tenant_b}"))
        await session.commit()

    service = QuoteDepositService(async_session_maker, IntegrationConnectionService(async_session_maker))
    with pytest.raises(QuoteNotFoundError):
        await service.preview(tenant_b, quote_a_id)
