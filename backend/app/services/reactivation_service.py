"""section 5: Database Reactivation. Deterministic candidate scoring only —
no LLM. A candidate is never contacted by this service directly; creating
a `ReactivationCandidate` is a read-only selection, same as Phase 4's
delay detection and Phase 5's overdue detection producing an exception
without sending anything itself.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.models.crm import Customer, Lead, LeadStatus
from app.models.marketing import ReactivationCampaign, ReactivationCandidate, ReactivationCandidateStatus
from app.models.operations import Job

CUSTOMER_INACTIVITY_DAYS = 180


class CampaignNotFoundError(Exception):
    pass


class ReactivationService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def create_campaign(self, tenant_id: uuid.UUID, *, name: str, target_criteria: str | None) -> ReactivationCampaign:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            row = ReactivationCampaign(tenant_id=tenant_id, name=name, target_criteria=target_criteria)
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return row

    async def identify_inactive_customers(
        self, tenant_id: uuid.UUID, campaign_id: uuid.UUID, *, as_of: datetime | None = None
    ) -> list[ReactivationCandidate]:
        """Customer's most recent job (any status) is older than
        `CUSTOMER_INACTIVITY_DAYS`, or the customer has never had a job at
        all despite existing — both are real, queryable conditions."""
        as_of = as_of or datetime.now(timezone.utc)
        cutoff = as_of - timedelta(days=CUSTOMER_INACTIVITY_DAYS)

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            campaign = await session.get(ReactivationCampaign, campaign_id)
            if campaign is None or campaign.tenant_id != tenant_id:
                raise CampaignNotFoundError("Reactivation campaign not found")

            last_job_per_customer = (
                await session.execute(
                    select(Job.customer_id, func.max(Job.created_at)).where(Job.tenant_id == tenant_id).group_by(Job.customer_id)
                )
            ).all()
            last_job_map = {row[0]: row[1] for row in last_job_per_customer}

            customers = (
                await session.execute(select(Customer).where(Customer.tenant_id == tenant_id))
            ).scalars().all()

            created: list[ReactivationCandidate] = []
            for customer in customers:
                last_job_at = last_job_map.get(customer.id)
                # sqlite (tests/dev) returns naive datetimes even for
                # DateTime(timezone=True) columns; Postgres does not. Treat
                # a naive value as UTC rather than letting the comparison
                # raise.
                if last_job_at is not None and last_job_at.tzinfo is None:
                    last_job_at = last_job_at.replace(tzinfo=timezone.utc)
                if last_job_at is not None and last_job_at > cutoff:
                    continue  # recently active — not a candidate

                reason = (
                    f"No job since {last_job_at.date().isoformat()} (>{CUSTOMER_INACTIVITY_DAYS} days)"
                    if last_job_at is not None
                    else "Customer has no job history on record"
                )

                existing = (
                    await session.execute(
                        select(ReactivationCandidate).where(
                            ReactivationCandidate.tenant_id == tenant_id,
                            ReactivationCandidate.campaign_id == campaign_id,
                            ReactivationCandidate.customer_id == customer.id,
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    continue

                candidate = ReactivationCandidate(
                    tenant_id=tenant_id, campaign_id=campaign_id, customer_id=customer.id, reason=reason,
                    score=50, status=ReactivationCandidateStatus.PENDING, identified_at=as_of,
                )
                session.add(candidate)
                created.append(candidate)

            await session.commit()
            for c in created:
                await session.refresh(c)
        return created

    async def identify_unbooked_qualified_leads(
        self, tenant_id: uuid.UUID, campaign_id: uuid.UUID, *, as_of: datetime | None = None
    ) -> list[ReactivationCandidate]:
        as_of = as_of or datetime.now(timezone.utc)
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            campaign = await session.get(ReactivationCampaign, campaign_id)
            if campaign is None or campaign.tenant_id != tenant_id:
                raise CampaignNotFoundError("Reactivation campaign not found")

            leads = (
                await session.execute(
                    select(Lead).where(Lead.tenant_id == tenant_id, Lead.status == LeadStatus.QUALIFIED)
                )
            ).scalars().all()

            created: list[ReactivationCandidate] = []
            for lead in leads:
                existing = (
                    await session.execute(
                        select(ReactivationCandidate).where(
                            ReactivationCandidate.tenant_id == tenant_id,
                            ReactivationCandidate.campaign_id == campaign_id,
                            ReactivationCandidate.lead_id == lead.id,
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    continue
                candidate = ReactivationCandidate(
                    tenant_id=tenant_id, campaign_id=campaign_id, lead_id=lead.id,
                    reason="Previously qualified but never booked", score=70,
                    status=ReactivationCandidateStatus.PENDING, identified_at=as_of,
                )
                session.add(candidate)
                created.append(candidate)

            await session.commit()
            for c in created:
                await session.refresh(c)
        return created
