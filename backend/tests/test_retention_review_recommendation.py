"""The Morning Brief's retention next-action loop — genuinely absent
before this: `RetentionSnapshot` only carried a raw count
(`open_retention_opportunities`), so the Morning Brief could observe
that retention opportunities existed but never recommend a specific one
by entity, and could never make an eligible review request executable
even though a safe, already-approval-gated tool
(`retention.send_review_request`) already existed for it. Reuses the
existing MorningBriefRecommendation/ToolRegistry/ApprovalRequest
architecture — no new recommendation or approval system."""

import uuid

import pytest

from app.db.session import async_session_maker
from app.models.actor import ActorType
from app.models.crm import Customer
from app.models.rbac import Role
from app.models.morning_brief import MorningBriefRecommendation
from app.models.retention import ReviewRequest, ReviewStatus
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id: uuid.UUID, role: Role = Role.OWNER) -> ExecutionContext:
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


async def _make_eligible_review_request(tenant_id: uuid.UUID) -> tuple[Customer, ReviewRequest]:
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Review Test Customer")
        session.add(customer)
        await session.flush()
        review = ReviewRequest(tenant_id=tenant_id, customer_id=customer.id, status=ReviewStatus.ELIGIBLE)
        session.add(review)
        await session.commit()
        await session.refresh(customer)
        await session.refresh(review)
    return customer, review


async def test_ai_recommends_an_executable_review_request(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer, review = await _make_eligible_review_request(tenant_id)

    await tool_registry.execute("insights.generate_morning_brief", {}, ctx)
    latest = await tool_registry.execute("insights.get_latest_morning_brief", {}, ctx)

    matches = [r for r in latest.recommendations if r.what == "Send a review request"]
    assert matches, [r.what for r in latest.recommendations]
    rec = matches[0]
    assert rec.related_entity_type == "customer"
    assert rec.related_entity_id == str(customer.id)
    assert rec.executable is True

    # The DTO returned to callers deliberately omits executable_tool/
    # executable_input (internal execution details) -- verify the real,
    # persisted row carries the correct tool + input instead.
    async with async_session_maker() as session:
        row = await session.get(MorningBriefRecommendation, uuid.UUID(rec.recommendation_id))
        assert row.executable_tool == "retention.send_review_request"
        assert row.executable_input == {"review_request_id": str(review.id)}


async def test_review_recommendation_execution_requires_approval(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    await _make_eligible_review_request(tenant_id)

    await tool_registry.execute("insights.generate_morning_brief", {}, ctx)
    latest = await tool_registry.execute("insights.get_latest_morning_brief", {}, ctx)
    rec = next(r for r in latest.recommendations if r.what == "Send a review request")

    result = await tool_registry.execute(
        "insights.execute_recommendation", {"recommendation_id": rec.recommendation_id}, ctx
    )
    assert result.status == "APPROVAL_REQUESTED"
    assert result.approval_request_id is not None


async def test_retention_recommendations_never_cross_tenants(tool_registry) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    ctx_b = _ctx(tenant_b)

    await _make_eligible_review_request(tenant_a)

    result = await tool_registry.execute("insights.get_retention_snapshot", {}, ctx_b)
    assert result.eligible_review_requests == []
    assert result.open_retention_opportunities_detail == []
