"""section 20/22: cross-CRM endpoints — today only, real cockpit metrics.

Every number here is computed from the database at request time. There is
no cached/fake/placeholder value: an empty tenant returns zeros for
everything, not sample data.
"""

from datetime import datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import CurrentUser, get_current_user
from app.db.session import async_session_maker, set_tenant_context
from app.models.crm import Appointment, AppointmentStatus, Lead, LeadStatus, QualificationStatus

router = APIRouter(prefix="/crm", tags=["crm"])


class CrmMetrics(BaseModel):
    new_leads_today: int
    qualified_leads: int
    appointments_today: int
    conversion_rate_pct: float
    uncontacted_leads: int
    at_risk_leads: int


@router.get("/metrics", response_model=CrmMetrics)
async def get_crm_metrics(current_user: CurrentUser = Depends(get_current_user)) -> CrmMetrics:
    tenant_id = current_user.tenant_id
    now = datetime.now(timezone.utc)
    day_start = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    async with async_session_maker() as session:
        await set_tenant_context(session, tenant_id)
        new_leads_today = (
            await session.execute(
                select(func.count(Lead.id)).where(
                    Lead.tenant_id == tenant_id, Lead.created_at >= day_start, Lead.created_at < day_end
                )
            )
        ).scalar_one()

        qualified_leads = (
            await session.execute(
                select(func.count(Lead.id)).where(
                    Lead.tenant_id == tenant_id,
                    Lead.qualification_status == QualificationStatus.QUALIFIED,
                )
            )
        ).scalar_one()

        appointments_today = (
            await session.execute(
                select(func.count(Appointment.id)).where(
                    Appointment.tenant_id == tenant_id,
                    Appointment.start_time >= day_start,
                    Appointment.start_time < day_end,
                    Appointment.status.in_([AppointmentStatus.TENTATIVE, AppointmentStatus.CONFIRMED]),
                )
            )
        ).scalar_one()

        total_leads = (
            await session.execute(select(func.count(Lead.id)).where(Lead.tenant_id == tenant_id))
        ).scalar_one()
        converted_leads = (
            await session.execute(
                select(func.count(Lead.id)).where(
                    Lead.tenant_id == tenant_id, Lead.status == LeadStatus.CONVERTED
                )
            )
        ).scalar_one()

        uncontacted_leads = (
            await session.execute(
                select(func.count(Lead.id)).where(
                    Lead.tenant_id == tenant_id, Lead.status == LeadStatus.NEW
                )
            )
        ).scalar_one()

        # "At risk" = a lead the scorer couldn't confidently place either way
        # and that nobody has acted on yet — needs a human before it goes cold.
        at_risk_leads = (
            await session.execute(
                select(func.count(Lead.id)).where(
                    Lead.tenant_id == tenant_id,
                    Lead.qualification_status == QualificationStatus.REQUIRES_HUMAN,
                    Lead.status == LeadStatus.NEW,
                )
            )
        ).scalar_one()

    conversion_rate = (converted_leads / total_leads * 100) if total_leads else 0.0

    return CrmMetrics(
        new_leads_today=new_leads_today,
        qualified_leads=qualified_leads,
        appointments_today=appointments_today,
        conversion_rate_pct=round(conversion_rate, 1),
        uncontacted_leads=uncontacted_leads,
        at_risk_leads=at_risk_leads,
    )
