"""section 22: the exception engine (operations slice).

Duplicate detection is real: `operations_exceptions` has a unique
constraint on (tenant_id, type, entity_id, status) — creating the same
open exception twice returns the existing row instead of duplicating it.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.events.bus import EventBus
from app.models.event import EventType
from app.models.operations import ExceptionStatus, OperationsException


class ExceptionNotFoundError(Exception):
    pass


class ExceptionService:
    def __init__(self, session_factory: async_sessionmaker, bus: EventBus) -> None:
        self._session_factory = session_factory
        self._bus = bus

    async def create_exception(
        self,
        tenant_id: uuid.UUID,
        *,
        type: str,
        severity: str,
        entity_type: str,
        entity_id: uuid.UUID,
        description: str,
        recommended_action: str | None = None,
        assigned_to: uuid.UUID | None = None,
    ) -> tuple[OperationsException, bool]:
        async with self._session_factory() as session:
            existing = (
                await session.execute(
                    select(OperationsException).where(
                        OperationsException.tenant_id == tenant_id,
                        OperationsException.type == type,
                        OperationsException.entity_id == entity_id,
                        OperationsException.status == ExceptionStatus.OPEN,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing, True

            exc = OperationsException(
                tenant_id=tenant_id,
                type=type,
                severity=severity,
                entity_type=entity_type,
                entity_id=entity_id,
                description=description,
                recommended_action=recommended_action,
                status=ExceptionStatus.OPEN,
                assigned_to=assigned_to,
            )
            session.add(exc)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = (
                    await session.execute(
                        select(OperationsException).where(
                            OperationsException.tenant_id == tenant_id,
                            OperationsException.type == type,
                            OperationsException.entity_id == entity_id,
                            OperationsException.status == ExceptionStatus.OPEN,
                        )
                    )
                ).scalar_one()
                return existing, True
            await session.refresh(exc)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.EXCEPTION_CREATED,
            source="operations",
            entity_type=entity_type,
            entity_id=entity_id,
            payload={"exception_id": str(exc.id), "type": type, "severity": severity},
        )
        return exc, False

    async def resolve_exception(self, tenant_id: uuid.UUID, exception_id: uuid.UUID) -> OperationsException:
        async with self._session_factory() as session:
            exc = await session.get(OperationsException, exception_id)
            if exc is None or exc.tenant_id != tenant_id:
                raise ExceptionNotFoundError("Exception not found")
            exc.status = ExceptionStatus.RESOLVED
            exc.resolved_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(exc)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.EXCEPTION_RESOLVED,
            source="operations",
            entity_type=exc.entity_type,
            entity_id=exc.entity_id,
            payload={"exception_id": str(exc.id)},
        )
        return exc
