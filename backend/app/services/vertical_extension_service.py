"""Phase 1 (KLAROS_PHASE_1_IMPLEMENTATION_PLAN.md §1.1): service layer for
the VerticalExtension registry and the tenant-scoped
OrganizationVerticalExtension join table.

No HTTP API is exposed over this service in Phase 1 — the plan's own
"Files/subsystems affected" for 1.1 is explicitly "new models/migration
only; no existing code changes." This service exists so the registry is
exercisable/testable (per 1.1's "Tests: registry CRUD tests") ahead of the
first real consumer (a later phase's Recommendation Engine / conditional
router mounting), matching the "smallest reference-data foundation first"
scope of Phase 1.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.models.vertical_extension import (
    OrganizationVerticalExtension,
    VerticalExtension,
    VerticalExtensionStatus,
)


class VerticalExtensionNotFoundError(Exception):
    pass


class VerticalExtensionAlreadyExistsError(Exception):
    pass


class VerticalExtensionService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def create_vertical(
        self,
        *,
        key: str,
        name: str,
        description: str | None = None,
        version: str = "1.0.0",
        status: str = VerticalExtensionStatus.BETA,
        capabilities: list | None = None,
        configuration_schema: dict | None = None,
        extra_metadata: dict | None = None,
    ) -> VerticalExtension:
        async with self._session_factory() as session:
            vertical = VerticalExtension(
                key=key,
                name=name,
                description=description,
                version=version,
                status=status,
                capabilities=capabilities or [],
                configuration_schema=configuration_schema,
                extra_metadata=extra_metadata,
            )
            session.add(vertical)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise VerticalExtensionAlreadyExistsError(
                    f"VerticalExtension with key {key!r} already exists"
                ) from exc
            await session.refresh(vertical)
            return vertical

    async def get_by_key(self, key: str) -> VerticalExtension:
        async with self._session_factory() as session:
            result = await session.execute(select(VerticalExtension).where(VerticalExtension.key == key))
            vertical = result.scalar_one_or_none()
            if vertical is None:
                raise VerticalExtensionNotFoundError(f"No VerticalExtension with key {key!r}")
            return vertical

    async def list_verticals(self, *, status: str | None = None) -> list[VerticalExtension]:
        async with self._session_factory() as session:
            query = select(VerticalExtension)
            if status is not None:
                query = query.where(VerticalExtension.status == status)
            query = query.order_by(VerticalExtension.key.asc())
            return list((await session.execute(query)).scalars().all())

    async def set_status(self, key: str, status: str) -> VerticalExtension:
        async with self._session_factory() as session:
            result = await session.execute(select(VerticalExtension).where(VerticalExtension.key == key))
            vertical = result.scalar_one_or_none()
            if vertical is None:
                raise VerticalExtensionNotFoundError(f"No VerticalExtension with key {key!r}")
            vertical.status = status
            await session.commit()
            await session.refresh(vertical)
            return vertical

    # --- Organization opt-in (tenant-scoped) -------------------------------
    # Phase 17B-2R classification: `VerticalExtension` itself (above) is a
    # GLOBAL/SHARED platform catalog table — no tenant_id column exists on
    # it at all (create_vertical/get_by_key/list_verticals/set_status
    # correctly never call set_tenant_context). `OrganizationVerticalExtension`
    # below IS genuinely tenant-scoped (the actual per-tenant opt-in row),
    # and its 3 session sites do call set_tenant_context.

    async def enable_for_organization(
        self, tenant_id: uuid.UUID, vertical_key: str, *, enabled_by: uuid.UUID | None = None
    ) -> OrganizationVerticalExtension:
        vertical = await self.get_by_key(vertical_key)
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            link = OrganizationVerticalExtension(
                tenant_id=tenant_id,
                vertical_extension_id=vertical.id,
                enabled_at=datetime.now(timezone.utc),
                enabled_by=enabled_by,
            )
            session.add(link)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise VerticalExtensionAlreadyExistsError(
                    f"Organization already has vertical {vertical_key!r} enabled"
                ) from exc
            await session.refresh(link)
            return link

    async def list_enabled_for_organization(self, tenant_id: uuid.UUID) -> list[OrganizationVerticalExtension]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            query = select(OrganizationVerticalExtension).where(
                OrganizationVerticalExtension.tenant_id == tenant_id
            )
            return list((await session.execute(query)).scalars().all())

    async def is_enabled_for_organization(self, tenant_id: uuid.UUID, vertical_key: str) -> bool:
        """The lookup mechanism core services are meant to use instead of
        branching on a vertical name — see app/models/vertical_extension.py's
        module docstring. No caller exists yet in Phase 1 (no consumer
        ships until a later phase), but the method is the documented,
        registry-backed shape any future caller must use."""
        vertical = await self.get_by_key(vertical_key)
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            query = select(OrganizationVerticalExtension).where(
                OrganizationVerticalExtension.tenant_id == tenant_id,
                OrganizationVerticalExtension.vertical_extension_id == vertical.id,
            )
            return (await session.execute(query)).scalar_one_or_none() is not None
