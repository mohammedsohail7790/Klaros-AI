"""section 4: Outbound & List Building. Duplicate prevention reuses the
*exact* normalization helpers Lead/Customer matching already uses — no
second normalization scheme, no fuzzy merge. Sequences use deterministic
`scheduled_for` records + on-demand execution, the same safe pattern as
Phase 5's `CollectionAction` — no `workflow.sleep()`. External sends never
happen while the provider is disconnected; `execute_due_activities` only
ever calls the *existing* `CommunicationProvider`.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.communications.base import CommunicationProvider, MessageTemplate
from app.db.session import set_tenant_context
from app.models.marketing import (
    ActivityStatus,
    EnrollmentStatus,
    OutboundActivity,
    OutboundContact,
    OutboundEnrollment,
    OutboundList,
    OutboundSequence,
    OutboundStep,
)
from app.services.customer_matching import normalize_email, normalize_phone


class ListNotFoundError(Exception):
    pass


class SequenceNotFoundError(Exception):
    pass


class ContactNotFoundError(Exception):
    pass


class DuplicateContactError(Exception):
    pass


class OutboundService:
    def __init__(self, session_factory: async_sessionmaker, comms: CommunicationProvider | None = None) -> None:
        self._session_factory = session_factory
        self._comms = comms

    async def create_list(self, tenant_id: uuid.UUID, *, name: str, description: str | None) -> OutboundList:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            row = OutboundList(tenant_id=tenant_id, name=name, description=description)
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return row

    async def add_contact(
        self, tenant_id: uuid.UUID, list_id: uuid.UUID, *, company: str | None, contact_name: str | None,
        email: str | None, phone: str | None, role: str | None = None, website: str | None = None,
        industry: str | None = None, location: str | None = None, service_relevance: str | None = None,
        source: str = "MANUAL",
    ) -> OutboundContact:
        from app.services.consent_gate import ensure_not_gated

        await ensure_not_gated(self._session_factory, tenant_id, "outbound_list_building_disabled_for_consent_gated_tenant")
        email_norm = normalize_email(email)
        phone_norm = normalize_phone(phone)

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            outbound_list = await session.get(OutboundList, list_id)
            if outbound_list is None or outbound_list.tenant_id != tenant_id:
                raise ListNotFoundError("Outbound list not found")

            if email_norm:
                existing = (
                    await session.execute(
                        select(OutboundContact).where(
                            OutboundContact.tenant_id == tenant_id, OutboundContact.email_normalized == email_norm
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    raise DuplicateContactError(f"A contact with email {email} already exists for this tenant")
            elif phone_norm:
                existing = (
                    await session.execute(
                        select(OutboundContact).where(
                            OutboundContact.tenant_id == tenant_id, OutboundContact.phone_normalized == phone_norm
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    raise DuplicateContactError(f"A contact with phone {phone} already exists for this tenant")

            contact = OutboundContact(
                tenant_id=tenant_id, list_id=list_id, company=company, contact_name=contact_name, role=role,
                email=email, email_normalized=email_norm, phone=phone, phone_normalized=phone_norm, website=website,
                industry=industry, location=location, service_relevance=service_relevance, source=source,
            )
            session.add(contact)
            await session.commit()
            await session.refresh(contact)
        return contact

    async def create_sequence(self, tenant_id: uuid.UUID, *, name: str, description: str | None) -> OutboundSequence:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            row = OutboundSequence(tenant_id=tenant_id, name=name, description=description)
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return row

    async def add_step(
        self, tenant_id: uuid.UUID, sequence_id: uuid.UUID, *, day_offset: int, channel: str, subject: str | None,
        body: str | None, sort_order: int = 0,
    ) -> OutboundStep:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            sequence = await session.get(OutboundSequence, sequence_id)
            if sequence is None or sequence.tenant_id != tenant_id:
                raise SequenceNotFoundError("Sequence not found")
            step = OutboundStep(
                tenant_id=tenant_id, sequence_id=sequence_id, day_offset=day_offset, channel=channel,
                subject=subject, body=body, sort_order=sort_order,
            )
            session.add(step)
            await session.commit()
            await session.refresh(step)
        return step

    async def enroll_contact(self, tenant_id: uuid.UUID, sequence_id: uuid.UUID, contact_id: uuid.UUID) -> OutboundEnrollment:
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            sequence = await session.get(OutboundSequence, sequence_id)
            if sequence is None or sequence.tenant_id != tenant_id:
                raise SequenceNotFoundError("Sequence not found")
            contact = await session.get(OutboundContact, contact_id)
            if contact is None or contact.tenant_id != tenant_id:
                raise ContactNotFoundError("Contact not found")

            existing = (
                await session.execute(
                    select(OutboundEnrollment).where(
                        OutboundEnrollment.tenant_id == tenant_id, OutboundEnrollment.sequence_id == sequence_id,
                        OutboundEnrollment.contact_id == contact_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing

            enrollment = OutboundEnrollment(
                tenant_id=tenant_id, sequence_id=sequence_id, contact_id=contact_id,
                status=EnrollmentStatus.ACTIVE, enrolled_at=now, current_step_index=0,
            )
            session.add(enrollment)
            await session.flush()

            steps = (
                await session.execute(
                    select(OutboundStep).where(
                        OutboundStep.tenant_id == tenant_id, OutboundStep.sequence_id == sequence_id
                    ).order_by(OutboundStep.sort_order)
                )
            ).scalars().all()
            for step in steps:
                session.add(
                    OutboundActivity(
                        tenant_id=tenant_id, enrollment_id=enrollment.id, step_id=step.id,
                        scheduled_for=now + timedelta(days=step.day_offset), status=ActivityStatus.PENDING,
                    )
                )
            await session.commit()
            await session.refresh(enrollment)
        return enrollment

    async def execute_due_activities(self, tenant_id: uuid.UUID) -> list[uuid.UUID]:
        """Deterministic, on-demand execution — no Temporal sleep. Sends a
        real message via the existing CommunicationProvider only; never
        while it's disconnected for real external channels (the internal
        test adapter is always 'connected')."""
        executed: list[uuid.UUID] = []
        now = datetime.now(timezone.utc)

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            due = (
                await session.execute(
                    select(OutboundActivity).where(
                        OutboundActivity.tenant_id == tenant_id, OutboundActivity.status == ActivityStatus.PENDING,
                        OutboundActivity.scheduled_for <= now,
                    )
                )
            ).scalars().all()

            for activity in due:
                enrollment = await session.get(OutboundEnrollment, activity.enrollment_id)
                if enrollment is None:
                    continue
                contact = await session.get(OutboundContact, enrollment.contact_id)
                step = await session.get(OutboundStep, activity.step_id)

                if self._comms is not None and contact and contact.email and step:
                    result = await self._comms.deliver_email(
                        tenant_id, to=contact.email, subject=step.subject or "Follow-up",
                        body=step.body or "", template=MessageTemplate.OUTBOUND_SEQUENCE_STEP,
                    )
                    if result.blocked:
                        activity.status = ActivityStatus.BLOCKED_CONSENT
                        continue

                activity.status = ActivityStatus.EXECUTED
                activity.executed_at = now
                executed.append(activity.id)

            await session.commit()
        return executed
