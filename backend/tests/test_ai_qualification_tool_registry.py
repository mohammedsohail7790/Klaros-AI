"""Phase 12E: crm.ai_qualify_lead_advisory through the REAL ToolRegistry
(built via the same build_tool_registry() the running app uses — not a
test-only construction), proving: AI-actor execution goes through the same
permission/tenant/audit pipeline as every other tool, the tool cannot
bypass ToolRegistry, and — since no credentials are configured in this
test environment — the real deterministic fallback path is exercised
honestly (never a fabricated recommendation)."""

import uuid

import pytest
from sqlalchemy import select

from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.crm import Lead
from app.models.rbac import Role
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


async def _make_lead(session_factory, tenant_id: uuid.UUID) -> Lead:
    async with session_factory() as session:
        lead = Lead(
            tenant_id=tenant_id,
            name="Test Customer",
            source="WEB",
            service_requested="Emergency commercial HVAC maintenance",
            description="Need urgent repair",
            urgency="HIGH",
            estimated_value=5000,
        )
        session.add(lead)
        await session.commit()
        await session.refresh(lead)
        return lead


async def test_ai_actor_execution_goes_through_toolregistry_and_produces_audit(tool_registry) -> None:
    from app.db.session import async_session_maker

    tenant_id = uuid.uuid4()
    lead = await _make_lead(async_session_maker, tenant_id)
    correlation_id = uuid.uuid4()

    context = ExecutionContext(
        tenant_id=tenant_id,
        actor_type=ActorType.AI,
        actor_id=None,
        role=Role.MANAGER,
        correlation_id=correlation_id,
    )
    output = await tool_registry.execute(
        "crm.ai_qualify_lead_advisory", {"lead_id": str(lead.id)}, context
    )

    # No credentials configured in this test environment — the real
    # deterministic fallback path is what actually runs; must be honest
    # about that, never a fabricated recommendation.
    assert output.available is False
    assert "no ai provider configured" in output.unavailable_reason.lower()

    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id == tenant_id, AuditLog.tool == "crm.ai_qualify_lead_advisory"
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].actor_type == "AI"
        assert rows[0].correlation_id == correlation_id


async def test_human_actor_can_also_call_the_same_tool(tool_registry) -> None:
    from app.db.session import async_session_maker

    tenant_id = uuid.uuid4()
    lead = await _make_lead(async_session_maker, tenant_id)
    user_id = uuid.uuid4()

    context = ExecutionContext(
        tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=user_id, role=Role.MANAGER
    )
    output = await tool_registry.execute(
        "crm.ai_qualify_lead_advisory", {"lead_id": str(lead.id)}, context
    )
    assert output.available is False  # still honest — no credentials either way


async def test_unauthorized_tenant_cannot_reach_another_tenants_lead_via_the_tool(tool_registry) -> None:
    from app.db.session import async_session_maker

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    lead = await _make_lead(async_session_maker, tenant_a)

    context_b = ExecutionContext(
        tenant_id=tenant_b, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=Role.MANAGER
    )
    with pytest.raises(ValueError, match="not found"):
        await tool_registry.execute("crm.ai_qualify_lead_advisory", {"lead_id": str(lead.id)}, context_b)

    async with async_session_maker() as session:
        from sqlalchemy import select

        from app.models.audit_log import AuditLog

        rows = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id == tenant_b, AuditLog.tool == "crm.ai_qualify_lead_advisory"
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].result == "failure"
