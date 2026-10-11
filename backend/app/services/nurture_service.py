"""section 5: Nurture & Database Reactivation (nurture half). Candidate
selection is deterministic — pure timestamp/status comparisons against the
*existing* `Lead` table, no LLM, mirroring Phase 4's delay detection and
Phase 5's overdue detection. Nothing is contacted without an explicit
enrollment; enrollment itself doesn't send anything — `execute_due_activities`
does, on demand, via the existing `CommunicationProvider`.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.communications.base import CommunicationProvider, MessageTemplate
from app.db.session import set_tenant_context
from app.models.crm import Lead, LeadStatus
from app.models.marketing import (
    ActivityStatus,
    EnrollmentStatus,
    NurtureActivity,
    NurtureEnrollment,
    NurtureSequence,
    NurtureTriggerType,
)
from app.models.event import EventType
from app.events.bus import EventBus

STALE_LEAD_DAYS = 30
UNBOOKED_QUALIFIED_DAYS = 14


class SequenceNotFoundError(Exception):
    pass


class NurtureService:
    def __init__(self, session_factory: async_sessionmaker, bus: EventBus, comms: CommunicationProvider | None = None) -> None:
        self._session_factory = session_factory
        self._bus = bus
        self._comms = comms

    async def create_sequence(self, tenant_id: uuid.UUID, *, name: str, trigger_type: str) -> NurtureSequence:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            row = NurtureSequence(tenant_id=tenant_id, name=name, trigger_type=trigger_type)
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return row

    async def find_stale_lead_candidates(self, tenant_id: uuid.UUID, *, as_of: datetime | None = None) -> list[Lead]:
        as_of = as_of or datetime.now(timezone.utc)
        cutoff = as_of - timedelta(days=STALE_LEAD_DAYS)
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            leads = (
                await session.execute(
                    select(Lead).where(
                        Lead.tenant_id == tenant_id,
                        Lead.status.in_((LeadStatus.NEW, LeadStatus.CONTACTED)),
                        Lead.created_at <= cutoff,
                    )
                )
            ).scalars().all()
        return list(leads)

    async def find_unbooked_qualified_candidates(self, tenant_id: uuid.UUID, *, as_of: datetime | None = None) -> list[Lead]:
        as_of = as_of or datetime.now(timezone.utc)
        cutoff = as_of - timedelta(days=UNBOOKED_QUALIFIED_DAYS)
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            leads = (
                await session.execute(
                    select(Lead).where(
                        Lead.tenant_id == tenant_id, Lead.status == LeadStatus.QUALIFIED, Lead.created_at <= cutoff,
                    )
                )
            ).scalars().all()
        return list(leads)

    async def enroll_lead(self, tenant_id: uuid.UUID, sequence_id: uuid.UUID, lead_id: uuid.UUID) -> NurtureEnrollment:
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            sequence = await session.get(NurtureSequence, sequence_id)
            if sequence is None or sequence.tenant_id != tenant_id:
                raise SequenceNotFoundError("Nurture sequence not found")

            existing = (
                await session.execute(
                    select(NurtureEnrollment).where(
                        NurtureEnrollment.tenant_id == tenant_id, NurtureEnrollment.sequence_id == sequence_id,
                        NurtureEnrollment.lead_id == lead_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing

            enrollment = NurtureEnrollment(
                tenant_id=tenant_id, sequence_id=sequence_id, lead_id=lead_id, status=EnrollmentStatus.ACTIVE,
                enrolled_at=now,
            )
            session.add(enrollment)
            await session.flush()
            session.add(
                NurtureActivity(
                    tenant_id=tenant_id, enrollment_id=enrollment.id, scheduled_for=now, channel="EMAIL",
                    status=ActivityStatus.PENDING,
                )
            )
            await session.commit()
            await session.refresh(enrollment)

        await self._bus.publish(
            tenant_id=tenant_id, event_type=EventType.MARKETING_NURTURE_STARTED, source="marketing",
            entity_type="nurture_enrollment", entity_id=enrollment.id,
            payload={"lead_id": str(lead_id), "sequence_id": str(sequence_id)},
        )
        return enrollment

    async def execute_due_activities(self, tenant_id: uuid.UUID) -> list[uuid.UUID]:
        executed: list[uuid.UUID] = []
        now = datetime.now(timezone.utc)

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            due = (
                await session.execute(
                    select(NurtureActivity).where(
                        NurtureActivity.tenant_id == tenant_id, NurtureActivity.status == ActivityStatus.PENDING,
                        NurtureActivity.scheduled_for <= now,
                    )
                )
            ).scalars().all()

            for activity in due:
                enrollment = await session.get(NurtureEnrollment, activity.enrollment_id)
                if enrollment is None:
                    continue
                lead = await session.get(Lead, enrollment.lead_id)

                if self._comms is not None and lead and lead.email:
                    result = await self._comms.deliver_email(
                        tenant_id, to=lead.email, lead_id=lead.id, subject="Still thinking it over?",
                        body=f"Following up on your {lead.service_requested or 'request'} — happy to help whenever you're ready.",
                        template=MessageTemplate.NURTURE_MESSAGE,
                    )
                    if result.blocked:
                        activity.status = ActivityStatus.BLOCKED_CONSENT
                        continue

                activity.status = ActivityStatus.EXECUTED
                activity.executed_at = now
                executed.append(activity.id)

            await session.commit()
        return executed
