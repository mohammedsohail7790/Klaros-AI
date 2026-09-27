"""Phase 1 (KLAROS_PHASE_1_IMPLEMENTATION_PLAN.md §1.2,
KLAROS_FINAL_INTEGRATION_MODEL.md): service layer for
`IntegrationProviderCatalog` — tenant-independent reference data, read by
every tenant, written only via `MANAGE_INTEGRATIONS_CATALOG`.

`derive_tenant_status` is the concrete enforcement of the hard constraint
in KLAROS_FINAL_INTEGRATION_MODEL.md: "the rendered UI status is
min(implementation_status-derived-ceiling, tenant-connection-derived-
status) — a provider whose implementation_status == STUB can never render
as CONNECTED, even if a tenant's IntegrationConnection.status was somehow
set to CONNECTED." This function is a pure read-time projection — it never
mutates `IntegrationConnection`, and a catalog read never depends on any
OTHER tenant's connection state (only the caller's own, passed in
explicitly), which is what keeps this a global catalog read rather than a
tenant-data leak.
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.integration_catalog import IntegrationProviderCatalog, ProviderImplementationStatus


class CatalogProviderNotFoundError(Exception):
    pass


class CatalogProviderAlreadyExistsError(Exception):
    pass


def derive_tenant_status(implementation_status: str, connection_status: str | None) -> str:
    """`connection_status` is the tenant's own `IntegrationConnection.status`
    (or None if the tenant has no connection row for this provider at all).
    Returns one rendered status string, never exposing raw connection
    details beyond the status itself."""
    if implementation_status == ProviderImplementationStatus.STUB:
        return "STUB"
    if implementation_status == ProviderImplementationStatus.WEBHOOK_NORMALIZER:
        return "WEBHOOK_NORMALIZER"
    # implementation_status == REAL: the tenant's own connection state is
    # the ceiling now — a REAL provider can legitimately show CONNECTED.
    if connection_status is None:
        return "NOT_CONNECTED"
    return connection_status


class IntegrationCatalogService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def list_catalog(self, *, category: str | None = None) -> list[IntegrationProviderCatalog]:
        async with self._session_factory() as session:
            query = select(IntegrationProviderCatalog)
            if category is not None:
                query = query.where(IntegrationProviderCatalog.category == category)
            query = query.order_by(IntegrationProviderCatalog.provider_key.asc())
            return list((await session.execute(query)).scalars().all())

    async def get_by_provider_key(self, provider_key: str) -> IntegrationProviderCatalog:
        async with self._session_factory() as session:
            result = await session.execute(
                select(IntegrationProviderCatalog).where(
                    IntegrationProviderCatalog.provider_key == provider_key
                )
            )
            entry = result.scalar_one_or_none()
            if entry is None:
                raise CatalogProviderNotFoundError(f"No catalog entry for provider_key {provider_key!r}")
            return entry

    async def create_entry(
        self,
        *,
        provider_key: str,
        display_name: str,
        category: str,
        implementation_status: str,
        auth_shape: str,
        description: str | None = None,
        recommended_for_verticals: list | None = None,
        health_check_strategy_ref: str | None = None,
    ) -> IntegrationProviderCatalog:
        async with self._session_factory() as session:
            entry = IntegrationProviderCatalog(
                provider_key=provider_key,
                display_name=display_name,
                category=category,
                implementation_status=implementation_status,
                auth_shape=auth_shape,
                description=description,
                recommended_for_verticals=recommended_for_verticals or [],
                health_check_strategy_ref=health_check_strategy_ref,
            )
            session.add(entry)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise CatalogProviderAlreadyExistsError(
                    f"Catalog entry for provider_key {provider_key!r} already exists"
                ) from exc
            await session.refresh(entry)
            return entry

    async def update_entry(
        self,
        provider_key: str,
        *,
        display_name: str | None = None,
        category: str | None = None,
        implementation_status: str | None = None,
        auth_shape: str | None = None,
        description: str | None = None,
        recommended_for_verticals: list | None = None,
        health_check_strategy_ref: str | None = None,
    ) -> IntegrationProviderCatalog:
        async with self._session_factory() as session:
            result = await session.execute(
                select(IntegrationProviderCatalog).where(
                    IntegrationProviderCatalog.provider_key == provider_key
                )
            )
            entry = result.scalar_one_or_none()
            if entry is None:
                raise CatalogProviderNotFoundError(f"No catalog entry for provider_key {provider_key!r}")
            if display_name is not None:
                entry.display_name = display_name
            if category is not None:
                entry.category = category
            if implementation_status is not None:
                entry.implementation_status = implementation_status
            if auth_shape is not None:
                entry.auth_shape = auth_shape
            if description is not None:
                entry.description = description
            if recommended_for_verticals is not None:
                entry.recommended_for_verticals = recommended_for_verticals
            if health_check_strategy_ref is not None:
                entry.health_check_strategy_ref = health_check_strategy_ref
            await session.commit()
            await session.refresh(entry)
            return entry
