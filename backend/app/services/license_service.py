"""Compliance tracking: licenses, insurance policies, bonds, and
certifications with real expiry dates. `detect_expiring` is the same
deterministic, on-demand sweep pattern as DelayDetectionService/
ContractService.detect_pending — it flips status and raises a real
OperationsException (deduplicated by the exception engine itself) rather
than inventing a second notification mechanism.
"""

import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.compliance import License, LicenseStatus
from app.models.operations import ExceptionSeverity, ExceptionType
from app.services.exception_service import ExceptionService

EXPIRING_SOON_WINDOW_DAYS = 30


class LicenseNotFoundError(Exception):
    pass


class LicenseService:
    def __init__(self, session_factory: async_sessionmaker, exceptions: ExceptionService) -> None:
        self._session_factory = session_factory
        self._exceptions = exceptions

    async def create_license(
        self,
        tenant_id: uuid.UUID,
        *,
        type: str,
        name: str,
        license_number: str | None,
        issuing_authority: str | None,
        holder_name: str | None,
        holder_user_id: uuid.UUID | None,
        issue_date: date | None,
        expiry_date: date,
        document_url: str | None,
        notes: str | None,
    ) -> License:
        status = LicenseStatus.EXPIRED if expiry_date < date.today() else LicenseStatus.ACTIVE
        async with self._session_factory() as session:
            lic = License(
                tenant_id=tenant_id, type=type, name=name, license_number=license_number,
                issuing_authority=issuing_authority, holder_name=holder_name, holder_user_id=holder_user_id,
                issue_date=issue_date, expiry_date=expiry_date, status=status,
                document_url=document_url, notes=notes,
            )
            session.add(lic)
            await session.commit()
            await session.refresh(lic)
        return lic

    async def list_licenses(
        self, tenant_id: uuid.UUID, *, status: str | None = None, type: str | None = None
    ) -> list[License]:
        async with self._session_factory() as session:
            query = select(License).where(License.tenant_id == tenant_id)
            if status:
                query = query.where(License.status == status)
            if type:
                query = query.where(License.type == type)
            query = query.order_by(License.expiry_date.asc())
            return list((await session.execute(query)).scalars().all())

    async def get(self, tenant_id: uuid.UUID, license_id: uuid.UUID) -> License:
        async with self._session_factory() as session:
            lic = await session.get(License, license_id)
            if lic is None or lic.tenant_id != tenant_id:
                raise LicenseNotFoundError("License not found")
            return lic

    async def renew(
        self,
        tenant_id: uuid.UUID,
        license_id: uuid.UUID,
        *,
        issue_date: date | None,
        expiry_date: date,
        document_url: str | None,
    ) -> License:
        """Renewing sets a fresh expiry date and clears any prior
        EXPIRING_SOON/EXPIRED status — the same "future date resets the
        clock" reasoning as `quotes.detect_expired_quotes` never touching
        a quote whose expiry has moved forward."""
        async with self._session_factory() as session:
            lic = await session.get(License, license_id)
            if lic is None or lic.tenant_id != tenant_id:
                raise LicenseNotFoundError("License not found")
            lic.issue_date = issue_date if issue_date is not None else lic.issue_date
            lic.expiry_date = expiry_date
            if document_url is not None:
                lic.document_url = document_url
            lic.status = LicenseStatus.EXPIRED if expiry_date < date.today() else LicenseStatus.ACTIVE
            await session.commit()
            await session.refresh(lic)
        return lic

    async def detect_expiring(self, tenant_id: uuid.UUID) -> dict[str, list[str]]:
        today = date.today()
        soon_cutoff = today + timedelta(days=EXPIRING_SOON_WINDOW_DAYS)
        newly_expiring: list[str] = []
        newly_expired: list[str] = []

        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(License).where(
                        License.tenant_id == tenant_id,
                        License.status != LicenseStatus.EXPIRED,
                        License.expiry_date <= soon_cutoff,
                    )
                )
            ).scalars().all()

            for lic in rows:
                if lic.expiry_date < today:
                    lic.status = LicenseStatus.EXPIRED
                    newly_expired.append(str(lic.id))
                elif lic.status != LicenseStatus.EXPIRING_SOON:
                    lic.status = LicenseStatus.EXPIRING_SOON
                    newly_expiring.append(str(lic.id))

            await session.commit()

        for lic_id in newly_expired:
            async with self._session_factory() as session:
                lic = await session.get(License, uuid.UUID(lic_id))
            await self._exceptions.create_exception(
                tenant_id,
                type=ExceptionType.LICENSE_EXPIRED,
                severity=ExceptionSeverity.CRITICAL,
                entity_type="license",
                entity_id=uuid.UUID(lic_id),
                description=f"{lic.name} ({lic.type}) expired on {lic.expiry_date.isoformat()}.",
                recommended_action="Renew immediately — operating on an expired license/insurance is a real business risk.",
            )

        for lic_id in newly_expiring:
            async with self._session_factory() as session:
                lic = await session.get(License, uuid.UUID(lic_id))
            await self._exceptions.create_exception(
                tenant_id,
                type=ExceptionType.LICENSE_EXPIRING_SOON,
                severity=ExceptionSeverity.MEDIUM,
                entity_type="license",
                entity_id=uuid.UUID(lic_id),
                description=f"{lic.name} ({lic.type}) expires on {lic.expiry_date.isoformat()}.",
                recommended_action="Start the renewal now — it will be marked EXPIRED once the date passes.",
            )

        return {"newly_expiring_soon": newly_expiring, "newly_expired": newly_expired}
