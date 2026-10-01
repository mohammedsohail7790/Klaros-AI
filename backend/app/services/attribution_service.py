"""section: Lead Attribution / Marketing Attribution Loop.

This is the most important service in Phase 6. `CampaignConversion` is
updated in place as a lead moves through the real business loop — never a
second row for the same (campaign, lead). Every derived metric
(`campaign_performance`) is computed from real rows only; when the
underlying data can't support a number (no spend, no qualified leads), the
result says so explicitly rather than returning `0` or a fabricated value.
`0` means "real rows exist and their sum is zero"; `None`/a message means
"not enough data to compute this."
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.models.crm import Lead
from app.models.finance import Invoice
from app.models.marketing import (
    AttributionModel,
    Campaign,
    CampaignConversion,
    CampaignLead,
    ConversionStage,
    LeadAttribution,
    MarketingSpendAllocation,
)
from app.models.operations import Job


class LeadNotFoundError(Exception):
    pass


class CampaignNotFoundError(Exception):
    pass


_STAGE_ORDER = [
    ConversionStage.LEAD,
    ConversionStage.QUALIFIED,
    ConversionStage.BOOKED,
    ConversionStage.JOB_CREATED,
    ConversionStage.JOB_CLOSED,
    ConversionStage.INVOICED,
    ConversionStage.PAID,
]


def _stage_index(stage: str) -> int:
    return _STAGE_ORDER.index(ConversionStage(stage))


@dataclass
class CampaignPerformance:
    campaign_id: uuid.UUID
    spend: Decimal
    leads: int
    qualified_leads: int
    booked: int
    jobs_created: int
    jobs_closed: int
    invoiced_count: int
    revenue: Decimal
    collected_revenue: Decimal
    cac: Decimal | None
    cac_note: str | None
    cost_per_qualified_lead: Decimal | None
    revenue_per_lead: Decimal | None
    roas: Decimal | None
    roas_note: str | None


class AttributionService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def attribute_lead(
        self,
        tenant_id: uuid.UUID,
        lead_id: uuid.UUID,
        *,
        campaign_id: uuid.UUID | None = None,
        source: str | None = None,
        medium: str | None = None,
        landing_page: str | None = None,
        referral_source: str | None = None,
        utm_source: str | None = None,
        utm_medium: str | None = None,
        utm_campaign: str | None = None,
        utm_term: str | None = None,
        utm_content: str | None = None,
        click_id: str | None = None,
        attribution_model: str = AttributionModel.SOURCE_ONLY,
    ) -> LeadAttribution:
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            lead = await session.get(Lead, lead_id)
            if lead is None or lead.tenant_id != tenant_id:
                raise LeadNotFoundError("Lead not found")

            if campaign_id is not None:
                campaign = await session.get(Campaign, campaign_id)
                if campaign is None or campaign.tenant_id != tenant_id:
                    raise CampaignNotFoundError("Campaign not found")

            existing = (
                await session.execute(
                    select(LeadAttribution).where(
                        LeadAttribution.tenant_id == tenant_id, LeadAttribution.lead_id == lead_id
                    )
                )
            ).scalar_one_or_none()

            if existing is None:
                attribution = LeadAttribution(
                    tenant_id=tenant_id, lead_id=lead_id, campaign_id=campaign_id, source=source, medium=medium,
                    landing_page=landing_page, referral_source=referral_source, utm_source=utm_source,
                    utm_medium=utm_medium, utm_campaign=utm_campaign, utm_term=utm_term, utm_content=utm_content,
                    click_id=click_id, first_touch_at=now, last_touch_at=now, attribution_model=attribution_model,
                )
                session.add(attribution)
            else:
                attribution = existing
                # LAST_TOUCH re-attributes to the newest campaign/source seen;
                # FIRST_TOUCH/SOURCE_ONLY never overwrite an existing claim.
                if attribution_model == AttributionModel.LAST_TOUCH:
                    attribution.campaign_id = campaign_id or attribution.campaign_id
                    attribution.source = source or attribution.source
                    attribution.medium = medium or attribution.medium
                    attribution.attribution_model = attribution_model
                attribution.last_touch_at = now

            await session.flush()

            if attribution.campaign_id is not None:
                dedup = (
                    await session.execute(
                        select(CampaignLead).where(
                            CampaignLead.tenant_id == tenant_id,
                            CampaignLead.campaign_id == attribution.campaign_id,
                            CampaignLead.lead_id == lead_id,
                        )
                    )
                ).scalar_one_or_none()
                if dedup is None:
                    session.add(
                        CampaignLead(
                            tenant_id=tenant_id, campaign_id=attribution.campaign_id, lead_id=lead_id, attributed_at=now
                        )
                    )
                    session.add(
                        CampaignConversion(
                            tenant_id=tenant_id, campaign_id=attribution.campaign_id, lead_id=lead_id,
                            stage=ConversionStage.LEAD, updated_at_stage=now,
                        )
                    )

            await session.commit()
            await session.refresh(attribution)
        return attribution

    async def _advance(self, tenant_id: uuid.UUID, lead_id: uuid.UUID, stage: str, **fields) -> None:
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            conversions = (
                await session.execute(
                    select(CampaignConversion).where(
                        CampaignConversion.tenant_id == tenant_id, CampaignConversion.lead_id == lead_id
                    )
                )
            ).scalars().all()
            for conv in conversions:
                if _stage_index(stage) > _stage_index(conv.stage):
                    conv.stage = stage
                    conv.updated_at_stage = now
                for key, value in fields.items():
                    if value is not None:
                        setattr(conv, key, value)
            await session.commit()

    async def mark_qualified(self, tenant_id: uuid.UUID, lead_id: uuid.UUID) -> None:
        await self._advance(tenant_id, lead_id, ConversionStage.QUALIFIED)

    async def mark_booked(self, tenant_id: uuid.UUID, lead_id: uuid.UUID) -> None:
        await self._advance(tenant_id, lead_id, ConversionStage.BOOKED)

    async def mark_job_created(self, tenant_id: uuid.UUID, lead_id: uuid.UUID, job_id: uuid.UUID, customer_id: uuid.UUID) -> None:
        await self._advance(tenant_id, lead_id, ConversionStage.JOB_CREATED, job_id=job_id, customer_id=customer_id)

    async def mark_job_closed(self, tenant_id: uuid.UUID, lead_id: uuid.UUID) -> None:
        await self._advance(tenant_id, lead_id, ConversionStage.JOB_CLOSED)

    async def mark_invoiced(self, tenant_id: uuid.UUID, lead_id: uuid.UUID, invoice_id: uuid.UUID, revenue_amount: Decimal) -> None:
        await self._advance(tenant_id, lead_id, ConversionStage.INVOICED, invoice_id=invoice_id, revenue_amount=revenue_amount)

    async def mark_paid(self, tenant_id: uuid.UUID, lead_id: uuid.UUID, collected_amount: Decimal) -> None:
        await self._advance(tenant_id, lead_id, ConversionStage.PAID, collected_amount=collected_amount)

    async def lead_id_for_job(self, tenant_id: uuid.UUID, job_id: uuid.UUID | None) -> uuid.UUID | None:
        if job_id is None:
            return None
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            job = await session.get(Job, job_id)
            if job is None or job.tenant_id != tenant_id:
                return None
            return job.lead_id

    async def lead_id_for_invoice(self, tenant_id: uuid.UUID, invoice_id: uuid.UUID | None) -> uuid.UUID | None:
        if invoice_id is None:
            return None
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            invoice = await session.get(Invoice, invoice_id)
            if invoice is None or invoice.tenant_id != tenant_id or invoice.job_id is None:
                return None
            job = await session.get(Job, invoice.job_id)
            if job is None:
                return None
            return job.lead_id

    async def campaign_performance(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID) -> CampaignPerformance:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            campaign = await session.get(Campaign, campaign_id)
            if campaign is None or campaign.tenant_id != tenant_id:
                raise CampaignNotFoundError("Campaign not found")

            spend_rows = (
                await session.execute(
                    select(MarketingSpendAllocation).where(
                        MarketingSpendAllocation.tenant_id == tenant_id,
                        MarketingSpendAllocation.campaign_id == campaign_id,
                    )
                )
            ).scalars().all()
            spend = sum((r.amount for r in spend_rows), Decimal("0"))

            conversions = (
                await session.execute(
                    select(CampaignConversion).where(
                        CampaignConversion.tenant_id == tenant_id, CampaignConversion.campaign_id == campaign_id
                    )
                )
            ).scalars().all()

        leads = len(conversions)
        qualified = sum(1 for c in conversions if _stage_index(c.stage) >= _stage_index(ConversionStage.QUALIFIED))
        booked = sum(1 for c in conversions if _stage_index(c.stage) >= _stage_index(ConversionStage.BOOKED))
        jobs_created = sum(1 for c in conversions if _stage_index(c.stage) >= _stage_index(ConversionStage.JOB_CREATED))
        jobs_closed = sum(1 for c in conversions if _stage_index(c.stage) >= _stage_index(ConversionStage.JOB_CLOSED))
        invoiced = sum(1 for c in conversions if _stage_index(c.stage) >= _stage_index(ConversionStage.INVOICED))
        revenue = sum((c.revenue_amount for c in conversions if c.revenue_amount is not None), Decimal("0"))
        collected = sum((c.collected_amount for c in conversions if c.collected_amount is not None), Decimal("0"))

        cac, cac_note = None, None
        if qualified == 0:
            cac_note = "Insufficient data: no qualified leads yet."
        elif spend == 0:
            cac_note = "Insufficient data: no spend recorded yet."
        else:
            cac = (spend / qualified).quantize(Decimal("0.01"))

        cost_per_qualified_lead = cac  # same calculation, kept as a distinct field per the spec's metric list

        revenue_per_lead = None
        if leads > 0:
            revenue_per_lead = (revenue / leads).quantize(Decimal("0.01"))

        roas, roas_note = None, None
        if spend == 0:
            roas_note = "Insufficient data: no spend recorded yet."
        elif leads == 0:
            roas_note = "Insufficient data: no leads attributed to this campaign yet."
        elif revenue == 0 and invoiced == 0:
            roas_note = "Attribution incomplete: no invoiced revenue yet for this campaign's leads."
        else:
            roas = (revenue / spend).quantize(Decimal("0.01"))

        return CampaignPerformance(
            campaign_id=campaign_id, spend=spend, leads=leads, qualified_leads=qualified, booked=booked,
            jobs_created=jobs_created, jobs_closed=jobs_closed, invoiced_count=invoiced, revenue=revenue,
            collected_revenue=collected, cac=cac, cac_note=cac_note, cost_per_qualified_lead=cost_per_qualified_lead,
            revenue_per_lead=revenue_per_lead, roas=roas, roas_note=roas_note,
        )
