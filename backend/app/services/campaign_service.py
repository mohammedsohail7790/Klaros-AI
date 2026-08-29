"""section 1/2: campaigns and spend. Budget alerts are deterministic
percentage-of-budget checks — no LLM. Real ad-platform budget changes are
never made automatically; a future `marketing.shift_budget` tool would be
a controlled, `APPROVAL_REQUIRED` action, same as everything else that
touches money in this codebase.
"""

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.events.bus import EventBus
from app.models.event import EventType
from app.models.marketing import Campaign, CampaignStatus, MarketingSpend, MarketingSpendAllocation
from app.models.operations import ExceptionType
from app.services.exception_service import ExceptionService

BUDGET_ALERT_THRESHOLDS = [Decimal("0.8"), Decimal("0.9"), Decimal("1.0")]
HIGH_CAC_THRESHOLD = Decimal("500.00")
LOW_CONVERSION_SPEND_THRESHOLD = Decimal("100.00")


class CampaignNotFoundError(Exception):
    pass


@dataclass
class BudgetStatus:
    budget: Decimal | None
    spend_to_date: Decimal
    remaining: Decimal | None
    utilization_pct: float | None
    alert: str | None


class CampaignService:
    def __init__(self, session_factory: async_sessionmaker, bus: EventBus, exception_service: ExceptionService) -> None:
        self._session_factory = session_factory
        self._bus = bus
        self._exception_service = exception_service

    async def create_campaign(
        self,
        tenant_id: uuid.UUID,
        *,
        name: str,
        channel: str,
        objective: str = "LEAD_GEN",
        monthly_budget: Decimal | None = None,
        total_budget: Decimal | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        external_provider: str | None = None,
    ) -> Campaign:
        async with self._session_factory() as session:
            campaign = Campaign(
                tenant_id=tenant_id,
                name=name,
                channel=channel,
                objective=objective,
                status=CampaignStatus.DRAFT,
                monthly_budget=monthly_budget,
                total_budget=total_budget,
                start_date=start_date,
                end_date=end_date,
                external_provider=external_provider,
            )
            session.add(campaign)
            await session.commit()
            await session.refresh(campaign)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.MARKETING_CAMPAIGN_CREATED,
            source="marketing",
            entity_type="campaign",
            entity_id=campaign.id,
            payload={"campaign_id": str(campaign.id), "name": name, "channel": channel},
        )
        return campaign

    async def set_status(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID, status: str) -> Campaign:
        async with self._session_factory() as session:
            campaign = await session.get(Campaign, campaign_id)
            if campaign is None or campaign.tenant_id != tenant_id:
                raise CampaignNotFoundError("Campaign not found")
            campaign.status = status
            await session.commit()
            await session.refresh(campaign)
        return campaign

    async def record_spend(
        self,
        tenant_id: uuid.UUID,
        *,
        channel: str,
        amount: Decimal,
        spend_date: date,
        allocations: list[tuple[uuid.UUID, Decimal]],
        source: str = "manual",
        external_reference: str | None = None,
    ) -> MarketingSpend:
        allocated_total = sum((amt for _, amt in allocations), Decimal("0"))
        if allocated_total > amount:
            raise ValueError(f"Allocations (${allocated_total}) exceed spend amount (${amount})")

        async with self._session_factory() as session:
            spend = MarketingSpend(
                tenant_id=tenant_id,
                channel=channel,
                amount=amount,
                spend_date=spend_date,
                source=source,
                external_reference=external_reference,
            )
            session.add(spend)
            await session.flush()

            campaign_ids: list[uuid.UUID] = []
            for campaign_id, alloc_amount in allocations:
                campaign = await session.get(Campaign, campaign_id)
                if campaign is None or campaign.tenant_id != tenant_id:
                    raise CampaignNotFoundError(f"Campaign {campaign_id} not found")
                session.add(
                    MarketingSpendAllocation(
                        tenant_id=tenant_id, spend_id=spend.id, campaign_id=campaign_id, amount=alloc_amount
                    )
                )
                campaign_ids.append(campaign_id)

            await session.commit()
            await session.refresh(spend)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.MARKETING_SPEND_RECORDED,
            source="marketing",
            entity_type="marketing_spend",
            entity_id=spend.id,
            payload={"spend_id": str(spend.id), "amount": str(amount), "campaign_ids": [str(c) for c in campaign_ids]},
        )

        for campaign_id in campaign_ids:
            await self._check_budget_alert(tenant_id, campaign_id)

        return spend

    async def budget_status(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID) -> BudgetStatus:
        async with self._session_factory() as session:
            campaign = await session.get(Campaign, campaign_id)
            if campaign is None or campaign.tenant_id != tenant_id:
                raise CampaignNotFoundError("Campaign not found")

            allocations = (
                await session.execute(
                    select(MarketingSpendAllocation).where(
                        MarketingSpendAllocation.tenant_id == tenant_id,
                        MarketingSpendAllocation.campaign_id == campaign_id,
                    )
                )
            ).scalars().all()

        spend_to_date = sum((a.amount for a in allocations), Decimal("0"))
        budget = campaign.total_budget or campaign.monthly_budget
        if budget is None or budget == 0:
            return BudgetStatus(budget=budget, spend_to_date=spend_to_date, remaining=None, utilization_pct=None, alert=None)

        remaining = budget - spend_to_date
        utilization = float(spend_to_date / budget)
        alert = None
        if utilization >= 1.0:
            alert = "overspend detected" if spend_to_date > budget else "100% budget consumed"
        elif utilization >= 0.9:
            alert = "90% budget consumed"
        elif utilization >= 0.8:
            alert = "80% budget consumed"

        return BudgetStatus(
            budget=budget, spend_to_date=spend_to_date, remaining=remaining, utilization_pct=round(utilization * 100, 1),
            alert=alert,
        )

    async def detect_performance_exceptions(self, tenant_id: uuid.UUID, attribution_service) -> list[uuid.UUID]:
        """HIGH_CAC (CAC over threshold) / LOW_CONVERSION (meaningful spend,
        zero qualified leads) — deterministic, on-demand, no LLM. Mirrors
        Phase 5's `ARService.detect_overdue` on-demand pattern."""
        flagged: list[uuid.UUID] = []
        async with self._session_factory() as session:
            campaigns = (await session.execute(select(Campaign).where(Campaign.tenant_id == tenant_id))).scalars().all()

        for campaign in campaigns:
            perf = await attribution_service.campaign_performance(tenant_id, campaign.id)
            if perf.cac is not None and perf.cac > HIGH_CAC_THRESHOLD:
                await self._exception_service.create_exception(
                    tenant_id, type=ExceptionType.HIGH_CAC, severity="MEDIUM", entity_type="campaign",
                    entity_id=campaign.id,
                    description=f"Campaign '{campaign.name}' CAC (${perf.cac}) exceeds ${HIGH_CAC_THRESHOLD}",
                    recommended_action="Review targeting/creative or pause the campaign",
                )
                flagged.append(campaign.id)
            elif perf.spend >= LOW_CONVERSION_SPEND_THRESHOLD and perf.qualified_leads == 0:
                await self._exception_service.create_exception(
                    tenant_id, type=ExceptionType.LOW_CONVERSION, severity="MEDIUM", entity_type="campaign",
                    entity_id=campaign.id,
                    description=f"Campaign '{campaign.name}' has spent ${perf.spend} with zero qualified leads",
                    recommended_action="Review lead quality/qualification criteria",
                )
                flagged.append(campaign.id)
        return flagged

    async def _check_budget_alert(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID) -> None:
        status = await self.budget_status(tenant_id, campaign_id)
        if status.alert and "overspend" in status.alert:
            await self._exception_service.create_exception(
                tenant_id,
                type=ExceptionType.CAMPAIGN_OVERSPEND,
                severity="HIGH",
                entity_type="campaign",
                entity_id=campaign_id,
                description=f"Campaign spend (${status.spend_to_date}) exceeds its budget (${status.budget})",
                recommended_action="Review campaign spend or increase budget",
            )
