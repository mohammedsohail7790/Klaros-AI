"""section: Retention Campaigns (POST_JOB_FOLLOWUP/SERVICE_REMINDER/
WIN_BACK/REVIEW_REQUEST/REFERRAL_INVITE/VIP_CUSTOMER/SERVICE_RECOVERY).
Enrollment schedules a real `RetentionActivity` (`scheduled_for` +
on-demand execution — same safe pattern as Phase 5/6, no
`workflow.sleep()`); execution sends through the *existing*
`CommunicationProvider` only.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.communications.base import CommunicationProvider, MessageTemplate
from app.db.session import set_tenant_context
from app.models.crm import Customer
from app.models.retention import (
    RetentionActivity,
    RetentionActivityStatus,
    RetentionCampaign,
    RetentionCampaignStatus,
    RetentionEnrollment,
    RetentionEnrollmentStatus,
)

_TEMPLATE_BY_TYPE = {
    "POST_JOB_FOLLOWUP": MessageTemplate.POST_JOB_FOLLOWUP,
    "SERVICE_REMINDER": MessageTemplate.SERVICE_REMINDER,
    "WIN_BACK": MessageTemplate.WIN_BACK,
    "REVIEW_REQUEST": MessageTemplate.REVIEW_REQUEST,
    "REFERRAL_INVITE": MessageTemplate.REFERRAL_INVITATION,
    "SERVICE_RECOVERY": MessageTemplate.SERVICE_RECOVERY,
    "VIP_CUSTOMER": MessageTemplate.POST_JOB_FOLLOWUP,
}


class CampaignNotFoundError(Exception):
    pass


class RetentionCampaignService:
    def __init__(self, session_factory: async_sessionmaker, comms: CommunicationProvider | None = None) -> None:
        self._session_factory = session_factory
        self._comms = comms

    async def create_campaign(self, tenant_id: uuid.UUID, *, name: str, type: str) -> RetentionCampaign:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            campaign = RetentionCampaign(tenant_id=tenant_id, name=name, type=type, status=RetentionCampaignStatus.DRAFT)
            session.add(campaign)
            await session.commit()
            await session.refresh(campaign)
        return campaign

    async def set_status(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID, status: str) -> RetentionCampaign:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            campaign = await session.get(RetentionCampaign, campaign_id)
            if campaign is None or campaign.tenant_id != tenant_id:
                raise CampaignNotFoundError("Retention campaign not found")
            campaign.status = status
            await session.commit()
            await session.refresh(campaign)
        return campaign

    async def enroll_customer(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID, customer_id: uuid.UUID) -> RetentionEnrollment:
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            campaign = await session.get(RetentionCampaign, campaign_id)
            if campaign is None or campaign.tenant_id != tenant_id:
                raise CampaignNotFoundError("Retention campaign not found")

            existing = (
                await session.execute(
                    select(RetentionEnrollment).where(
                        RetentionEnrollment.tenant_id == tenant_id, RetentionEnrollment.campaign_id == campaign_id,
                        RetentionEnrollment.customer_id == customer_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing

            enrollment = RetentionEnrollment(
                tenant_id=tenant_id, campaign_id=campaign_id, customer_id=customer_id,
                status=RetentionEnrollmentStatus.ACTIVE, enrolled_at=now,
            )
            session.add(enrollment)
            await session.flush()
            session.add(
                RetentionActivity(
                    tenant_id=tenant_id, enrollment_id=enrollment.id, scheduled_for=now, channel="EMAIL",
                    template=campaign.type, status=RetentionActivityStatus.PENDING,
                )
            )
            await session.commit()
            await session.refresh(enrollment)
        return enrollment

    async def execute_due_activities(self, tenant_id: uuid.UUID) -> list[uuid.UUID]:
        executed: list[uuid.UUID] = []
        now = datetime.now(timezone.utc)

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            due = (
                await session.execute(
                    select(RetentionActivity).where(
                        RetentionActivity.tenant_id == tenant_id, RetentionActivity.status == RetentionActivityStatus.PENDING,
                        RetentionActivity.scheduled_for <= now,
                    )
                )
            ).scalars().all()

            for activity in due:
                enrollment = await session.get(RetentionEnrollment, activity.enrollment_id)
                if enrollment is None:
                    continue
                customer = await session.get(Customer, enrollment.customer_id)

                if self._comms is not None and customer and customer.email:
                    template = _TEMPLATE_BY_TYPE.get(activity.template or "", MessageTemplate.POST_JOB_FOLLOWUP)
                    result = await self._comms.deliver_email(
                        tenant_id, to=customer.email, customer_id=customer.id, subject="A note from your service team",
                        body=f"Reaching out regarding your recent service ({activity.template or 'follow-up'}).",
                        template=template,
                    )
                    if result.blocked:
                        activity.status = RetentionActivityStatus.BLOCKED_CONSENT
                        continue

                activity.status = RetentionActivityStatus.EXECUTED
                activity.executed_at = now
                executed.append(activity.id)

            await session.commit()
        return executed
