"""section 25/47: wires the existing CommunicationProvider into Operations.

This was the Phase 3 gap called out honestly in PROJECT_STATUS.md — the
communication adapter existed but nothing called it. Phase 4 closes that
for job status changes specifically (booking-time CRM communication is
still not wired — out of this phase's scope, still listed as a gap).
"""

import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.communications.base import CommunicationProvider, MessageTemplate
from app.db.session import set_tenant_context
from app.models.crm import Customer
from app.models.operations import Job


class OperationsCommunicationService:
    def __init__(self, session_factory: async_sessionmaker, provider: CommunicationProvider) -> None:
        self._session_factory = session_factory
        self._provider = provider

    async def _customer_email(self, tenant_id: uuid.UUID, customer_id: uuid.UUID) -> str | None:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            customer = await session.get(Customer, customer_id)
            if customer is None or customer.tenant_id != tenant_id:
                return None
            return customer.email

    async def notify_job_scheduled(self, tenant_id: uuid.UUID, job: Job) -> bool:
        email = await self._customer_email(tenant_id, job.customer_id)
        if not email:
            return False
        return await self._provider.send_email(
            tenant_id,
            to=email,
            subject=f"Your appointment is scheduled — {job.title}",
            body=f"Job {job.job_number} ('{job.title}') is scheduled for {job.scheduled_start}.",
            template=MessageTemplate.APPOINTMENT_CONFIRMATION,
        )

    async def notify_job_dispatched(self, tenant_id: uuid.UUID, job: Job) -> bool:
        email = await self._customer_email(tenant_id, job.customer_id)
        if not email:
            return False
        return await self._provider.send_email(
            tenant_id,
            to=email,
            subject=f"Your technician is on the way — {job.title}",
            body=f"A technician has been dispatched for job {job.job_number}.",
            template=MessageTemplate.APPOINTMENT_REMINDER,
        )

    async def notify_job_en_route(self, tenant_id: uuid.UUID, job: Job) -> bool:
        email = await self._customer_email(tenant_id, job.customer_id)
        if not email:
            return False
        return await self._provider.send_email(
            tenant_id,
            to=email,
            subject=f"Your technician is en route — {job.title}",
            body=f"Your technician is now en route for job {job.job_number}.",
            template=MessageTemplate.APPOINTMENT_REMINDER,
        )

    async def notify_job_completed(self, tenant_id: uuid.UUID, job: Job) -> bool:
        email = await self._customer_email(tenant_id, job.customer_id)
        if not email:
            return False
        return await self._provider.send_email(
            tenant_id,
            to=email,
            subject=f"Job completed — {job.title}",
            body=f"Job {job.job_number} has been completed. Thank you for your business.",
            template=MessageTemplate.APPOINTMENT_CONFIRMATION,
        )
