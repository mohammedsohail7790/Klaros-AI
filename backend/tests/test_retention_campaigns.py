"""Retention Campaigns: create/status/enroll/execute-due, plus the
status/type validation fix (same bug class as
retention_service.update_reminder_status,
operations.update_worker_status, crm.update_customer/update_lead,
operations.update_job priority)."""

import uuid

import pytest

from app.models.actor import ActorType
from app.models.rbac import Role
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id, role=Role.OWNER):
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


async def test_create_campaign_lands_in_draft(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    out = await tool_registry.execute(
        "retention.create_campaign", {"name": "Fall Win-Back", "type": "WIN_BACK"}, _ctx(tenant_id)
    )
    assert out.status == "DRAFT"


async def test_create_campaign_rejects_unknown_type(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    with pytest.raises(ValueError, match="Invalid retention campaign type"):
        await tool_registry.execute(
            "retention.create_campaign", {"name": "Bad", "type": "NOT_A_REAL_TYPE"}, _ctx(tenant_id)
        )


async def test_set_campaign_status_rejects_unknown_status(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    created = await tool_registry.execute(
        "retention.create_campaign", {"name": "Win-Back", "type": "WIN_BACK"}, ctx
    )
    with pytest.raises(ValueError, match="Invalid retention campaign status"):
        await tool_registry.execute(
            "retention.set_campaign_status",
            {"campaign_id": created.campaign_id, "status": "NOT_A_REAL_STATUS"},
            ctx,
        )


async def test_set_campaign_status_updates_a_real_campaign(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    created = await tool_registry.execute(
        "retention.create_campaign", {"name": "Win-Back", "type": "WIN_BACK"}, ctx
    )
    updated = await tool_registry.execute(
        "retention.set_campaign_status", {"campaign_id": created.campaign_id, "status": "ACTIVE"}, ctx
    )
    assert updated.status == "ACTIVE"


async def test_enroll_customer_schedules_a_pending_activity_and_execute_due_runs_it(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    campaign = await tool_registry.execute(
        "retention.create_campaign", {"name": "Win-Back", "type": "WIN_BACK"}, ctx
    )
    customer = await tool_registry.execute("crm.create_customer", {"name": "Enrollee"}, ctx)

    enrollment = await tool_registry.execute(
        "retention.enroll_customer_in_campaign",
        {"campaign_id": campaign.campaign_id, "customer_id": customer.customer["id"]},
        ctx,
    )
    assert enrollment.status == "ACTIVE"

    # Enrolling the same customer twice is idempotent -- returns the same enrollment.
    again = await tool_registry.execute(
        "retention.enroll_customer_in_campaign",
        {"campaign_id": campaign.campaign_id, "customer_id": customer.customer["id"]},
        ctx,
    )
    assert again.enrollment_id == enrollment.enrollment_id

    executed = await tool_registry.execute("retention.execute_due_activities", {}, ctx)
    assert len(executed.executed_activity_ids) == 1


async def test_enroll_customer_in_unknown_campaign_rejected(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer = await tool_registry.execute("crm.create_customer", {"name": "Nobody"}, ctx)
    with pytest.raises(ValueError, match="not found"):
        await tool_registry.execute(
            "retention.enroll_customer_in_campaign",
            {"campaign_id": str(uuid.uuid4()), "customer_id": customer.customer["id"]},
            ctx,
        )
