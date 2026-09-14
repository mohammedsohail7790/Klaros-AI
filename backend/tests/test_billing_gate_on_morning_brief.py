"""End-to-end: ToolRegistry.execute() actually enforces the billing gate
on insights.generate_morning_brief (Tool.counts_toward_ai_usage) for a
real Organization row — the counterpart to test_billing_service.py's
unit-level BillingService tests. A tenant_id with NO Organization row
(the pattern the rest of this test suite uses) must remain ungated, for
backward compatibility."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.db.session import async_session_maker
from app.models.actor import ActorType
from app.models.organization import Organization
from app.models.rbac import Role
from app.tools.base import ExecutionContext
from app.tools.errors import ToolBillingLimitError

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id, role=Role.OWNER):
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


async def _make_org(**overrides) -> Organization:
    async with async_session_maker() as session:
        org = Organization(
            name=f"Gate Test Co {uuid.uuid4().hex[:6]}",
            slug=f"gate-test-{uuid.uuid4().hex}",
            plan=overrides.pop("plan", "growth"),
            billing_status=overrides.pop("billing_status", "trialing"),
            trial_ends_at=overrides.pop("trial_ends_at", datetime.now(UTC) + timedelta(days=14)),
        )
        session.add(org)
        await session.commit()
        await session.refresh(org)
        return org


async def test_tenant_with_no_organization_row_is_never_gated(tool_registry) -> None:
    tenant_id = uuid.uuid4()  # no Organization row — matches every other morning-brief test in this suite
    out = await tool_registry.execute("insights.generate_morning_brief", {}, _ctx(tenant_id))
    assert out.headline


async def test_expired_trial_blocks_generate_morning_brief_over_the_registry(tool_registry) -> None:
    org = await _make_org(billing_status="trialing", trial_ends_at=datetime.now(UTC) - timedelta(days=1))
    with pytest.raises(ToolBillingLimitError):
        await tool_registry.execute("insights.generate_morning_brief", {}, _ctx(org.id))


async def test_active_growth_org_can_still_generate(tool_registry) -> None:
    org = await _make_org(plan="growth", billing_status="active", trial_ends_at=None)
    out = await tool_registry.execute("insights.generate_morning_brief", {}, _ctx(org.id))
    assert out.headline
