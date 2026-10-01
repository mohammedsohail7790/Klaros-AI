"""Phase 17B-2R: real-PostgreSQL behavioral proof that the 1
independently-opened session in app/tools/builtin/stripe_tools.py
(CreateStripeCheckoutSession) now stamps `SET LOCAL app.tenant_id`."""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.integrations.stripe_client import StripeClient
from app.models.actor import ActorType
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


async def _make_customer_and_invoice(tool_registry, tenant_id: uuid.UUID) -> str:
    ctx = _ctx(tenant_id)
    customer = await tool_registry.execute("crm.create_customer", {"name": f"Stripe Ctx {uuid.uuid4().hex[:6]}"}, ctx)
    invoice = await tool_registry.execute(
        "finance.create_invoice_draft",
        {
            "customer_id": customer.customer["id"],
            "line_items": [{"description": "Job", "quantity": "1", "unit_price": "100.00"}],
        },
        ctx,
    )
    return invoice.invoice["id"]


@requires_real_postgres
async def test_create_checkout_session_sets_tenant_context(monkeypatch, spy, tool_registry) -> None:
    import app.tools.builtin.stripe_tools as stripe_tools_module
    from app.integrations.stripe_schemas import StripeCheckoutSessionResponse

    monkeypatch.setattr(stripe_tools_module, "set_tenant_context", spy)

    settings = get_settings()
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_bogus_key_0123456789")

    async def _fake_create_checkout_session(self, **kwargs):
        return StripeCheckoutSessionResponse(id="cs_test_ctx1", url="https://checkout.stripe.com/cs_test_ctx1")

    monkeypatch.setattr(StripeClient, "create_checkout_session", _fake_create_checkout_session)

    tenant_id = uuid.uuid4()
    invoice_id = await _make_customer_and_invoice(tool_registry, tenant_id)
    ctx = _ctx(tenant_id)

    result = await tool_registry.execute(
        "finance.create_stripe_checkout_session",
        {"invoice_id": invoice_id, "success_url": "https://example.com/success", "cancel_url": "https://example.com/cancel"},
        ctx,
    )
    assert result.checkout_url

    assert len(spy.calls) >= 1
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_invoice_never_checkoutable_by_tenant_b(monkeypatch, tool_registry) -> None:
    from app.tools.errors import ToolError

    settings = get_settings()
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_bogus_key_0123456789")

    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    invoice_id_a = await _make_customer_and_invoice(tool_registry, tenant_a)

    with pytest.raises(ToolError, match="not found"):
        await tool_registry.execute(
            "finance.create_stripe_checkout_session",
            {"invoice_id": invoice_id_a, "success_url": "https://example.com/success", "cancel_url": "https://example.com/cancel"},
            _ctx(tenant_b),
        )
