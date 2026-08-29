"""Focused Marketing unit/integration tests: budget alerts, tenant
isolation, outbound contact dedup, content approval boundary, reactivation
candidate selection, and NOT_CONNECTED ads/outbound providers."""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.integrations.base import ConnectionStatus
from app.marketing_ads.base import NotConnectedGoogleAdsAdapter, NotConnectedMetaAdsAdapter
from app.marketing_outbound.base import NotConnectedApolloAdapter, NotConnectedClayAdapter
from app.models.actor import ActorType
from app.models.marketing import ContentStatus
from app.models.rbac import Role
from app.tools.base import ExecutionContext

def _ctx(tenant_id, role=Role.OWNER):
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


def test_ads_providers_report_not_connected() -> None:
    for adapter in (NotConnectedGoogleAdsAdapter(), NotConnectedMetaAdsAdapter()):
        status = adapter.get_status()
        assert status.status == ConnectionStatus.NOT_CONNECTED


def test_outbound_providers_report_not_connected() -> None:
    for adapter in (NotConnectedClayAdapter(), NotConnectedApolloAdapter()):
        status = adapter.get_status()
        assert status.status == ConnectionStatus.NOT_CONNECTED


@pytest.mark.asyncio
async def test_budget_alert_thresholds(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    campaign = await tool_registry.execute(
        "marketing.create_campaign", {"name": "Budget Test", "channel": "GOOGLE_ADS", "total_budget": "100.00"}, ctx
    )
    campaign_id = campaign.campaign["id"]

    await tool_registry.execute(
        "marketing.record_spend",
        {
            "channel": "GOOGLE_ADS", "amount": "85.00", "spend_date": date.today().isoformat(),
            "allocations": [{"campaign_id": campaign_id, "amount": "85.00"}],
        },
        ctx,
    )

    status = await tool_registry.execute("marketing.get_budget_status", {"campaign_id": campaign_id}, ctx)
    assert status.alert == "80% budget consumed"

    await tool_registry.execute(
        "marketing.record_spend",
        {
            "channel": "GOOGLE_ADS", "amount": "20.00", "spend_date": date.today().isoformat(),
            "allocations": [{"campaign_id": campaign_id, "amount": "20.00"}],
        },
        ctx,
    )
    status = await tool_registry.execute("marketing.get_budget_status", {"campaign_id": campaign_id}, ctx)
    assert status.alert == "overspend detected"

    from app.models.operations import ExceptionType, OperationsException

    async with event_bus.session_factory() as session:
        exc = (
            await session.execute(
                select(OperationsException).where(
                    OperationsException.tenant_id == tenant_id, OperationsException.type == ExceptionType.CAMPAIGN_OVERSPEND,
                    OperationsException.entity_id == uuid.UUID(campaign_id),
                )
            )
        ).scalar_one_or_none()
    assert exc is not None


@pytest.mark.asyncio
async def test_marketing_tools_enforce_tenant_isolation(event_bus, tool_registry) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    ctx_a = _ctx(tenant_a)
    ctx_b = _ctx(tenant_b)

    campaign = await tool_registry.execute("marketing.create_campaign", {"name": "Tenant A Campaign", "channel": "GOOGLE_ADS"}, ctx_a)
    campaign_id = campaign.campaign["id"]

    with pytest.raises(ValueError):
        await tool_registry.execute("marketing.get_budget_status", {"campaign_id": campaign_id}, ctx_b)

    with pytest.raises(ValueError):
        await tool_registry.execute("marketing.set_campaign_status", {"campaign_id": campaign_id, "status": "PAUSED"}, ctx_b)


@pytest.mark.asyncio
async def test_outbound_contact_duplicate_rejected(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    outbound_list = await tool_registry.execute("marketing.create_outbound_list", {"name": "Local Contractors"}, ctx)
    list_id = outbound_list.list_id

    await tool_registry.execute(
        "marketing.add_outbound_contact",
        {"list_id": list_id, "company": "Acme Roofing", "email": "contact@acmeroofing.com"}, ctx,
    )

    with pytest.raises(ValueError):
        await tool_registry.execute(
            "marketing.add_outbound_contact",
            {"list_id": list_id, "company": "Acme Roofing (dup)", "email": "Contact@AcmeRoofing.com"}, ctx,
        )


@pytest.mark.asyncio
async def test_content_approval_boundary_ai_cannot_self_approve(event_bus, tool_registry) -> None:
    """AI's default role in this codebase is whatever ExecutionContext.role
    is set to by the caller — this test proves the *permission* boundary
    itself (APPROVE_MARKETING_CONTENT) is enforced regardless of actor,
    by using a role that legitimately lacks it (READ_ONLY)."""
    tenant_id = uuid.uuid4()
    owner_ctx = _ctx(tenant_id, role=Role.OWNER)
    read_only_ctx = _ctx(tenant_id, role=Role.READ_ONLY)

    idea = await tool_registry.execute("marketing.create_content_idea", {"title": "Before/after HVAC repair"}, owner_ctx)
    content_id = idea.content["id"]
    await tool_registry.execute("marketing.request_content_approval", {"content_id": content_id}, owner_ctx)

    from app.tools.errors import ToolPermissionError

    with pytest.raises(ToolPermissionError):
        await tool_registry.execute("marketing.approve_content", {"content_id": content_id}, read_only_ctx)

    approved = await tool_registry.execute("marketing.approve_content", {"content_id": content_id}, owner_ctx)
    assert approved.content["status"] == ContentStatus.APPROVED


@pytest.mark.asyncio
async def test_reactivation_identifies_inactive_customer_deterministically(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    customer = await tool_registry.execute("crm.create_customer", {"name": "Old Customer"}, ctx)
    customer_id = customer.customer["id"]

    job_out = await tool_registry.execute("operations.create_job", {"title": "Old job", "customer_id": customer_id}, ctx)
    job_id = job_out.job["id"]

    # Force the job's created_at into the distant past (>180 days) directly —
    # no tool exposes backdating, and this simulates real historical data.
    async with event_bus.session_factory() as session:
        from app.models.operations import Job

        job = await session.get(Job, uuid.UUID(job_id))
        job.created_at = datetime.now(timezone.utc) - timedelta(days=200)
        await session.commit()

    campaign = await tool_registry.execute(
        "marketing.create_reactivation_campaign", {"name": "Q3 Win-back", "target_criteria": "No job in 180+ days"}, ctx
    )
    campaign_id = campaign.campaign_id

    result = await tool_registry.execute("marketing.identify_inactive_customers", {"campaign_id": campaign_id}, ctx)
    assert len(result.candidate_ids) == 1

    # Running it again must not create a duplicate candidate for the same customer.
    result_again = await tool_registry.execute("marketing.identify_inactive_customers", {"campaign_id": campaign_id}, ctx)
    assert len(result_again.candidate_ids) == 0
