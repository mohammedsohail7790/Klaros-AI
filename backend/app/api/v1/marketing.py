"""/marketing dashboard summary — real, derived-on-request numbers only,
same honesty rule as Phase 5's /finance/summary. Aggregates across every
campaign for the tenant; when there isn't enough real data for CAC/ROAS,
says so explicitly instead of returning 0 or a fabricated number."""

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.api.deps import CurrentUser, get_current_user
from app.db.session import async_session_maker
from app.marketing_ads.base import (
    NotConnectedGoogleAdsAdapter,
    NotConnectedLocalServicesAdsAdapter,
    NotConnectedMetaAdsAdapter,
    NotConnectedYouTubeAdsAdapter,
)
from app.models.marketing import Campaign
from app.models.operations import ExceptionStatus, OperationsException
from app.services.attribution_service import AttributionService

router = APIRouter(prefix="/marketing", tags=["marketing"])

FINANCE_TAG_EXCEPTION_TYPES = (
    "CAMPAIGN_OVERSPEND", "LOW_CONVERSION", "HIGH_CAC", "ATTRIBUTION_GAP",
    "CONTENT_APPROVAL_DELAY", "FAILED_PUBLICATION", "REACTIVATION_FAILURE",
)

_ADS_ADAPTERS = [
    NotConnectedGoogleAdsAdapter(), NotConnectedMetaAdsAdapter(),
    NotConnectedYouTubeAdsAdapter(), NotConnectedLocalServicesAdsAdapter(),
]


@router.get("/summary")
async def marketing_summary(current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    tenant_id = current_user.tenant_id
    attribution_service = AttributionService(async_session_maker)

    async with async_session_maker() as session:
        campaigns = (await session.execute(select(Campaign).where(Campaign.tenant_id == tenant_id))).scalars().all()

        open_exceptions = (
            await session.execute(
                select(func.count(OperationsException.id)).where(
                    OperationsException.tenant_id == tenant_id,
                    OperationsException.status == ExceptionStatus.OPEN,
                    OperationsException.type.in_(FINANCE_TAG_EXCEPTION_TYPES),
                )
            )
        ).scalar_one()

    spend = Decimal("0")
    leads = 0
    qualified = 0
    booked = 0
    jobs_won = 0
    revenue = Decimal("0")
    collected = Decimal("0")

    for campaign in campaigns:
        perf = await attribution_service.campaign_performance(tenant_id, campaign.id)
        spend += perf.spend
        leads += perf.leads
        qualified += perf.qualified_leads
        booked += perf.booked
        jobs_won += perf.jobs_closed
        revenue += perf.revenue
        collected += perf.collected_revenue

    cac = None
    cac_note = None
    if qualified == 0:
        cac_note = "Insufficient data: no qualified leads yet."
    elif spend == 0:
        cac_note = "Insufficient data: no spend recorded yet."
    else:
        cac = str((spend / qualified).quantize(Decimal("0.01")))

    roas = None
    roas_note = None
    if spend == 0:
        roas_note = "Insufficient data: no spend recorded yet."
    elif revenue == 0:
        roas_note = "Attribution incomplete: no invoiced revenue yet."
    else:
        roas = str((revenue / spend).quantize(Decimal("0.01")))

    conversion_rate = round((qualified / leads) * 100, 1) if leads > 0 else None

    return {
        "campaign_count": len(campaigns),
        "marketing_spend": str(spend),
        "leads": leads,
        "qualified_leads": qualified,
        "appointments_booked": booked,
        "jobs_won": jobs_won,
        "revenue": str(revenue),
        "collected_revenue": str(collected),
        "cac": cac,
        "cac_note": cac_note,
        "roas": roas,
        "roas_note": roas_note,
        "conversion_rate_pct": conversion_rate,
        "open_marketing_exception_count": open_exceptions,
        "needs_attention": open_exceptions > 0,
    }


@router.get("/ads-provider-status")
async def ads_provider_status() -> dict[str, Any]:
    return {
        "providers": [
            {"provider": s.provider, "status": s.status, "detail": s.detail}
            for s in (a.get_status() for a in _ADS_ADAPTERS)
        ]
    }
