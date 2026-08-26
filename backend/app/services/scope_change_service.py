"""section 21: scope changes.

`create_scope_change` only ever records a detected change and computes its
estimated margin impact — it never touches `Job.estimated_revenue/cost`
itself ("do not silently modify job pricing"). Applying an approved scope
change to the job's actual figures is a Finance-module concern (Phase 5),
not built here.
"""

import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.events.bus import EventBus
from app.models.event import EventType
from app.models.operations import Job, ScopeChange, ScopeChangeStatus


class JobNotFoundError(Exception):
    pass


class ScopeChangeService:
    def __init__(self, session_factory: async_sessionmaker, bus: EventBus) -> None:
        self._session_factory = session_factory
        self._bus = bus

    async def create_scope_change(
        self,
        tenant_id: uuid.UUID,
        job_id: uuid.UUID,
        *,
        description: str,
        reason: str | None,
        estimated_cost: float | None,
        estimated_revenue: float | None,
        created_by: uuid.UUID | None,
    ) -> ScopeChange:
        async with self._session_factory() as session:
            job = await session.get(Job, job_id)
            if job is None or job.tenant_id != tenant_id:
                raise JobNotFoundError("Job not found")

            margin_impact = None
            if estimated_cost is not None and estimated_revenue is not None:
                margin_impact = float(estimated_revenue) - float(estimated_cost)

            has_financial_impact = estimated_cost or estimated_revenue

            scope_change = ScopeChange(
                tenant_id=tenant_id,
                job_id=job_id,
                description=description,
                reason=reason,
                estimated_cost=estimated_cost,
                estimated_revenue=estimated_revenue,
                margin_impact=margin_impact,
                status=(
                    ScopeChangeStatus.PENDING_APPROVAL if has_financial_impact else ScopeChangeStatus.DETECTED
                ),
                created_by=created_by,
            )
            session.add(scope_change)
            await session.commit()
            await session.refresh(scope_change)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.SCOPE_CHANGE_DETECTED,
            source="operations",
            entity_type="job",
            entity_id=job_id,
            payload={"scope_change_id": str(scope_change.id), "margin_impact": margin_impact},
        )
        return scope_change
