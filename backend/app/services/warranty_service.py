"""Warranty tracking — the "warranty check-in" retention touchpoint from
the One-Person Company diagram. `detect_expiring` is the same
deterministic on-demand sweep pattern as LicenseService/
DelayDetectionService: flip status, raise a real OperationsException
(deduplicated by the exception engine itself), no second mechanism.
Unlike a license, an expired warranty is not a compliance risk to the
business — only EXPIRING_SOON raises an exception (it's the window where
reaching out is still useful), while EXPIRED is just a status flip.
"""

import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.models.operations import ExceptionSeverity, ExceptionType
from app.models.retention import Warranty, WarrantyStatus
from app.services.exception_service import ExceptionService

EXPIRING_SOON_WINDOW_DAYS = 30


class WarrantyNotFoundError(Exception):
    pass


class WarrantyService:
    def __init__(self, session_factory: async_sessionmaker, exceptions: ExceptionService) -> None:
        self._session_factory = session_factory
        self._exceptions = exceptions

    async def create_warranty(
        self,
        tenant_id: uuid.UUID,
        *,
        customer_id: uuid.UUID,
        job_id: uuid.UUID | None,
        item_description: str,
        start_date: date,
        expiry_date: date,
        notes: str | None,
    ) -> Warranty:
        status = WarrantyStatus.EXPIRED if expiry_date < date.today() else WarrantyStatus.ACTIVE
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            warranty = Warranty(
                tenant_id=tenant_id, customer_id=customer_id, job_id=job_id, item_description=item_description,
                start_date=start_date, expiry_date=expiry_date, status=status, notes=notes,
            )
            session.add(warranty)
            await session.commit()
            await session.refresh(warranty)
        return warranty

    async def list_warranties(
        self, tenant_id: uuid.UUID, *, customer_id: uuid.UUID | None = None, status: str | None = None
    ) -> list[Warranty]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            query = select(Warranty).where(Warranty.tenant_id == tenant_id)
            if customer_id:
                query = query.where(Warranty.customer_id == customer_id)
            if status:
                query = query.where(Warranty.status == status)
            query = query.order_by(Warranty.expiry_date.asc())
            return list((await session.execute(query)).scalars().all())

    async def check_in(self, tenant_id: uuid.UUID, warranty_id: uuid.UUID, *, notes: str | None) -> Warranty:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            warranty = await session.get(Warranty, warranty_id)
            if warranty is None or warranty.tenant_id != tenant_id:
                raise WarrantyNotFoundError("Warranty not found")
            warranty.last_checked_in_at = datetime.now(timezone.utc)
            if notes:
                warranty.notes = notes
            await session.commit()
            await session.refresh(warranty)
        return warranty

    async def detect_expiring(self, tenant_id: uuid.UUID) -> dict[str, list[str]]:
        today = date.today()
        soon_cutoff = today + timedelta(days=EXPIRING_SOON_WINDOW_DAYS)
        newly_expiring: list[str] = []
        newly_expired: list[str] = []

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            rows = (
                await session.execute(
                    select(Warranty).where(
                        Warranty.tenant_id == tenant_id,
                        Warranty.status.notin_([WarrantyStatus.EXPIRED, WarrantyStatus.CLAIMED]),
                        Warranty.expiry_date <= soon_cutoff,
                    )
                )
            ).scalars().all()

            for w in rows:
                if w.expiry_date < today:
                    w.status = WarrantyStatus.EXPIRED
                    newly_expired.append(str(w.id))
                elif w.status != WarrantyStatus.EXPIRING_SOON:
                    w.status = WarrantyStatus.EXPIRING_SOON
                    newly_expiring.append(str(w.id))

            await session.commit()

        for w_id in newly_expiring:
            async with self._session_factory() as session:
                await set_tenant_context(session, tenant_id)
                w = await session.get(Warranty, uuid.UUID(w_id))
            await self._exceptions.create_exception(
                tenant_id,
                type=ExceptionType.WARRANTY_EXPIRING_SOON,
                severity=ExceptionSeverity.LOW,
                entity_type="warranty",
                entity_id=uuid.UUID(w_id),
                description=f"Warranty on {w.item_description} expires on {w.expiry_date.isoformat()}.",
                recommended_action="Check in with the customer before it lapses — a real retention/upsell touchpoint.",
            )

        return {"newly_expiring_soon": newly_expiring, "newly_expired": newly_expired}
