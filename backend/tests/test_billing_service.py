"""Klaros's own SaaS subscription billing: plan/trial gating on the
metered AI-recommendation feature (insights.generate_morning_brief),
and BillingService.get_billing_status's usage counting."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.db.session import async_session_maker
from app.models.ai_invocation import AIInvocationLog
from app.models.organization import Organization
from app.services.billing_service import BillingService
from app.tools.errors import ToolBillingLimitError

pytestmark = pytest.mark.asyncio


async def _make_org(**overrides) -> Organization:
    async with async_session_maker() as session:
        org = Organization(
            name=f"Billing Test Co {uuid.uuid4().hex[:6]}",
            slug=f"billing-test-{uuid.uuid4().hex}",
            plan=overrides.pop("plan", "growth"),
            billing_status=overrides.pop("billing_status", "trialing"),
            trial_ends_at=overrides.pop("trial_ends_at", datetime.now(UTC) + timedelta(days=14)),
            **overrides,
        )
        session.add(org)
        await session.commit()
        await session.refresh(org)
        return org


async def _record_brief_usage(tenant_id: uuid.UUID, count: int) -> None:
    async with async_session_maker() as session:
        for _ in range(count):
            session.add(
                AIInvocationLog(
                    tenant_id=tenant_id,
                    actor_type="ai",
                    provider="anthropic",
                    model="test-model",
                    operation="morning_brief_enrichment",
                    success=True,
                    latency_ms=10,
                )
            )
        await session.commit()


async def test_active_trial_allows_usage_regardless_of_plan() -> None:
    org = await _make_org(plan="solo", billing_status="trialing", trial_ends_at=datetime.now(UTC) + timedelta(days=1))
    await _record_brief_usage(org.id, 500)  # far past the solo cap — trial ignores it

    service = BillingService(async_session_maker)
    await service.check_ai_usage_allowed(org)  # must not raise


async def test_expired_trial_blocks_usage() -> None:
    org = await _make_org(billing_status="trialing", trial_ends_at=datetime.now(UTC) - timedelta(days=1))

    service = BillingService(async_session_maker)
    with pytest.raises(ToolBillingLimitError, match="trial has ended"):
        await service.check_ai_usage_allowed(org)


async def test_solo_plan_blocks_after_cap_reached() -> None:
    org = await _make_org(plan="solo", billing_status="active", trial_ends_at=None)
    await _record_brief_usage(org.id, 50)

    service = BillingService(async_session_maker)
    with pytest.raises(ToolBillingLimitError, match="upgrade to Growth"):
        await service.check_ai_usage_allowed(org)


async def test_solo_plan_allows_usage_under_cap() -> None:
    org = await _make_org(plan="solo", billing_status="active", trial_ends_at=None)
    await _record_brief_usage(org.id, 49)

    service = BillingService(async_session_maker)
    await service.check_ai_usage_allowed(org)  # must not raise


async def test_growth_plan_is_unlimited() -> None:
    org = await _make_org(plan="growth", billing_status="active", trial_ends_at=None)
    await _record_brief_usage(org.id, 500)

    service = BillingService(async_session_maker)
    await service.check_ai_usage_allowed(org)  # must not raise


async def test_past_due_subscription_blocks_usage() -> None:
    org = await _make_org(plan="growth", billing_status="past_due", trial_ends_at=None)

    service = BillingService(async_session_maker)
    with pytest.raises(ToolBillingLimitError, match="not active"):
        await service.check_ai_usage_allowed(org)


async def test_get_billing_status_reports_usage_and_limit() -> None:
    org = await _make_org(plan="solo", billing_status="active", trial_ends_at=None)
    await _record_brief_usage(org.id, 12)

    service = BillingService(async_session_maker)
    result = await service.get_billing_status(org)

    assert result.ai_usage_this_month == 12
    assert result.ai_usage_limit == 50
    assert result.plan == "solo"
    assert result.billing_status == "active"
