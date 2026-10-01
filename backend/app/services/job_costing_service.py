"""section 18-19: job costing. Feeds the *existing* `Job.actual_cost` /
`Job.actual_margin` fields (Phase 4, float-mapped) — this deliberately does
NOT add new cost fields to Job, per spec section 18: "Do not duplicate
these fields unnecessarily." `JobCost` rows are the detail; the Job's own
columns are always the recomputed sum/derived margin, kept in sync here.
"""

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.models.event import EventType
from app.models.operations import ExceptionType, Job, JobMaterial
from app.models.finance import JobCost
from app.services.exception_service import ExceptionService

MARGIN_LEAK_THRESHOLD_POINTS = Decimal("10")  # margin dropping >10 points below estimate is a leak


class JobNotFoundError(Exception):
    pass


class JobCostingService:
    def __init__(self, session_factory: async_sessionmaker, exception_service: ExceptionService) -> None:
        self._session_factory = session_factory
        self._exception_service = exception_service

    async def record_cost(
        self,
        tenant_id: uuid.UUID,
        *,
        job_id: uuid.UUID,
        category: str,
        description: str | None,
        quantity: Decimal,
        unit_cost: Decimal,
        source: str = "manual",
        vendor_id: uuid.UUID | None = None,
    ) -> JobCost:
        total_cost = (quantity * unit_cost).quantize(Decimal("0.01"))
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            job = await session.get(Job, job_id)
            if job is None or job.tenant_id != tenant_id:
                raise JobNotFoundError("Job not found")

            cost = JobCost(
                tenant_id=tenant_id,
                job_id=job_id,
                category=category,
                description=description,
                quantity=quantity,
                unit_cost=unit_cost,
                total_cost=total_cost,
                source=source,
                vendor_id=vendor_id,
            )
            session.add(cost)
            await session.flush()
            await session.commit()
            await session.refresh(cost)

        await self.recalculate_job_actuals(tenant_id, job_id)
        return cost

    async def sync_material_costs(self, tenant_id: uuid.UUID, job_id: uuid.UUID) -> int:
        """Creates a MATERIAL JobCost row for any JobMaterial that has an
        actual_unit_cost but no corresponding cost row yet (source='material_sync')."""
        created = 0
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            materials = (
                await session.execute(
                    select(JobMaterial).where(
                        JobMaterial.tenant_id == tenant_id,
                        JobMaterial.job_id == job_id,
                        JobMaterial.actual_unit_cost.is_not(None),
                    )
                )
            ).scalars().all()

            existing_sources = {
                c.description
                for c in (
                    await session.execute(
                        select(JobCost).where(
                            JobCost.tenant_id == tenant_id,
                            JobCost.job_id == job_id,
                            JobCost.source == "material_sync",
                        )
                    )
                ).scalars().all()
            }

            for material in materials:
                tag = f"material:{material.id}"
                if tag in existing_sources:
                    continue
                qty = Decimal(str(material.quantity))
                unit_cost = Decimal(str(material.actual_unit_cost))
                session.add(
                    JobCost(
                        tenant_id=tenant_id,
                        job_id=job_id,
                        category="MATERIAL",
                        description=tag,
                        quantity=qty,
                        unit_cost=unit_cost,
                        total_cost=(qty * unit_cost).quantize(Decimal("0.01")),
                        source="material_sync",
                    )
                )
                created += 1
            await session.commit()

        if created:
            await self.recalculate_job_actuals(tenant_id, job_id)
        return created

    async def recalculate_job_actuals(self, tenant_id: uuid.UUID, job_id: uuid.UUID) -> Job:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            job = await session.get(Job, job_id)
            if job is None or job.tenant_id != tenant_id:
                raise JobNotFoundError("Job not found")

            costs = (
                await session.execute(
                    select(JobCost).where(JobCost.tenant_id == tenant_id, JobCost.job_id == job_id)
                )
            ).scalars().all()
            total_actual_cost = sum((c.total_cost for c in costs), Decimal("0"))

            job.actual_cost = float(total_actual_cost)
            if job.actual_revenue:
                job.actual_margin = float(Decimal(str(job.actual_revenue)) - total_actual_cost)
            await session.commit()
            await session.refresh(job)

        await self._check_margin_leak(tenant_id, job)
        return job

    async def _check_margin_leak(self, tenant_id: uuid.UUID, job: Job) -> None:
        if not job.estimated_revenue or not job.estimated_cost:
            return
        estimated_margin_pct = (
            (Decimal(str(job.estimated_revenue)) - Decimal(str(job.estimated_cost)))
            / Decimal(str(job.estimated_revenue))
            * 100
        )
        revenue = job.actual_revenue or job.estimated_revenue
        if not revenue or job.actual_cost is None:
            return
        actual_margin_pct = (Decimal(str(revenue)) - Decimal(str(job.actual_cost))) / Decimal(str(revenue)) * 100

        if estimated_margin_pct - actual_margin_pct > MARGIN_LEAK_THRESHOLD_POINTS:
            await self._exception_service.create_exception(
                tenant_id,
                type=ExceptionType.MARGIN_LEAK,
                severity="HIGH",
                entity_type="job",
                entity_id=job.id,
                description=(
                    f"Job {job.job_number} margin dropped from an estimated "
                    f"{estimated_margin_pct:.1f}% to an actual {actual_margin_pct:.1f}%"
                ),
                recommended_action="Review job costs against the original estimate",
            )
