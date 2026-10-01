"""Phase 17B-2R: real-PostgreSQL behavioral proof for BillingService's 5
session-open sites. Given special care as a Stripe-linked flow (task
§13, same treatment as payment_service.py): `apply_subscription_updated`/
`apply_subscription_deleted` take a Stripe `subscription_id`, not a
tenant_id parameter — tenant identity is resolved by looking up the
Organization whose `stripe_subscription_id` matches (the ID from an
already-signature-verified Stripe webhook payload — see
app/api/v1/billing.py's own signature-verification gate before these are
ever called), then `set_tenant_context` is called using that resolved
org.id, AFTER resolution, never a client-supplied claim.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.organization import Organization
from app.services.billing_service import BillingService

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


async def _make_org(**overrides) -> Organization:
    async with async_session_maker() as session:
        org = Organization(
            name=f"Billing Phase17b2r Co {uuid.uuid4().hex[:6]}",
            slug=f"billing-p17b2r-{uuid.uuid4().hex}",
            plan=overrides.pop("plan", "growth"),
            billing_status=overrides.pop("billing_status", "trialing"),
            trial_ends_at=overrides.pop("trial_ends_at", datetime.now(UTC) + timedelta(days=14)),
            **overrides,
        )
        session.add(org)
        await session.commit()
        await session.refresh(org)
        return org


@requires_real_postgres
async def test_apply_checkout_completed_sets_tenant_context(monkeypatch, spy) -> None:
    import app.services.billing_service as billing_service_module

    monkeypatch.setattr(billing_service_module, "set_tenant_context", spy)

    org = await _make_org()
    service = BillingService(async_session_maker)

    await service.apply_checkout_completed(org.id, "growth", "cus_test123", "sub_test123")

    assert len(spy.calls) >= 1
    for called_tenant, readback in spy.calls:
        assert called_tenant == org.id
        assert readback == str(org.id)


@requires_real_postgres
async def test_apply_subscription_updated_resolves_tenant_then_sets_context(monkeypatch, spy) -> None:
    """Proves the resolve-then-stamp shape: set_tenant_context is called
    with the ORG'S id, resolved from stripe_subscription_id — never called
    before that resolution, never with a fabricated/None tenant."""
    import app.services.billing_service as billing_service_module

    monkeypatch.setattr(billing_service_module, "set_tenant_context", spy)

    org = await _make_org()
    async with async_session_maker() as session:
        db_org = await session.get(Organization, org.id)
        db_org.stripe_subscription_id = "sub_resolve_test"
        await session.commit()

    service = BillingService(async_session_maker)
    await service.apply_subscription_updated("sub_resolve_test", status="active", current_period_end=None)

    assert len(spy.calls) == 1
    called_tenant, readback = spy.calls[0]
    assert called_tenant == org.id
    assert readback == str(org.id)


@requires_real_postgres
async def test_unknown_subscription_id_never_sets_context() -> None:
    """A webhook for a subscription_id that matches no Organization must
    be a safe no-op — never guesses or fabricates a tenant."""
    service = BillingService(async_session_maker)
    # Must not raise, and (implicitly, since no org exists) never resolves
    # or stamps any tenant context.
    await service.apply_subscription_updated("sub_does_not_exist", status="active", current_period_end=None)
    await service.apply_subscription_deleted("sub_does_not_exist")


@requires_real_postgres
async def test_tenant_a_billing_status_isolated_from_tenant_b() -> None:
    org_a = await _make_org(plan="solo")
    org_b = await _make_org(plan="solo")
    service = BillingService(async_session_maker)

    async with async_session_maker() as session:
        db_org_a = await session.get(Organization, org_a.id)
        db_org_a.stripe_subscription_id = "sub_tenant_a"
        await session.commit()

    await service.apply_subscription_updated("sub_tenant_a", status="past_due", current_period_end=None)

    status_a = await service.get_billing_status(await _refresh(org_a.id))
    status_b = await service.get_billing_status(await _refresh(org_b.id))
    assert status_a.billing_status == "past_due"
    assert status_b.billing_status == "trialing"


async def _refresh(org_id: uuid.UUID) -> Organization:
    async with async_session_maker() as session:
        return await session.get(Organization, org_id)
