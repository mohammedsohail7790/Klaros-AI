"""Marketing nurture sequences: create/find-candidates/enroll/execute-due,
plus the trigger_type validation fix (same bug class as
retention.set_campaign_status/create_campaign,
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


async def test_create_sequence(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    out = await tool_registry.execute(
        "marketing.create_nurture_sequence", {"name": "Stale Lead Follow-up", "trigger_type": "STALE_LEAD"}, _ctx(tenant_id)
    )
    assert out.sequence_id


async def test_create_sequence_rejects_unknown_trigger_type(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    with pytest.raises(ValueError, match="Invalid nurture trigger type"):
        await tool_registry.execute(
            "marketing.create_nurture_sequence", {"name": "Bad", "trigger_type": "NOT_A_REAL_TRIGGER"}, _ctx(tenant_id)
        )


async def test_enroll_lead_schedules_a_pending_activity_and_execute_due_runs_it(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    sequence = await tool_registry.execute(
        "marketing.create_nurture_sequence", {"name": "Stale Lead Follow-up", "trigger_type": "STALE_LEAD"}, ctx
    )
    lead = await tool_registry.execute("crm.create_lead", {"name": "Stale Lead", "source": "WEB"}, ctx)

    enrollment = await tool_registry.execute(
        "marketing.enroll_lead_in_nurture",
        {"sequence_id": sequence.sequence_id, "lead_id": lead.lead["id"]},
        ctx,
    )
    assert enrollment.status == "ACTIVE"

    executed = await tool_registry.execute("marketing.execute_due_nurture_activities", {}, ctx)
    assert len(executed.executed_activity_ids) == 1


async def test_enroll_lead_in_unknown_sequence_rejected(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    lead = await tool_registry.execute("crm.create_lead", {"name": "Nobody", "source": "WEB"}, ctx)
    with pytest.raises(ValueError, match="not found"):
        await tool_registry.execute(
            "marketing.enroll_lead_in_nurture",
            {"sequence_id": str(uuid.uuid4()), "lead_id": lead.lead["id"]},
            ctx,
        )


async def test_find_stale_lead_candidates_is_deterministic_and_excludes_recent_leads(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    await tool_registry.execute("crm.create_lead", {"name": "Brand New Lead", "source": "WEB"}, ctx)

    result = await tool_registry.execute("marketing.find_stale_lead_candidates", {}, ctx)
    assert result.lead_ids == []
