"""Phase 11 (PHASE_11_WEBSITE_BUILDER_DESIGN.md): the Website Builder
service — owns Website/WebsiteVersion/WebsitePage/WebsiteSection CRUD,
full-row versioning (clone-on-edit-after-publish, mirroring
BusinessBlueprintService's own versioning shape), the preview read path,
and the publish/unpublish transaction.

Tenant isolation: every method takes an explicit `tenant_id` and filters
every query by it — the same unenforced-by-DB-RLS convention every other
Phase 2/3/10 service already documents (app/api/deps.py's
set_tenant_context docstring).

Immutability: every method that mutates a page/section/theme/navigation
first loads the owning WebsiteVersion and raises
`WebsiteVersionImmutableError` unless its status is DRAFT — this is the
actual enforcement mechanism behind design doc §14's "a published version
is immutable," not just a documented convention (verified by
tests/test_website_publishing_lifecycle.py attempting a real mutation).

Every website-created / version-created / version-updated / generation-
requested / publish / publish-failure event is one `AuditLog` row —
reusing the existing table, mirroring BusinessBlueprintService's/
RecommendationService's exact "one canonical audit write path" convention;
no second audit table is introduced (Phase 11).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.website import Website, WebsitePage, WebsiteSection, WebsiteVersion, WebsiteVersionStatus
from app.schemas.website_specification import (
    NavigationSpec,
    PageSpec,
    SeoMetadata,
    SectionSpec,
    ThemeTokens,
    WebsiteSpecification,
)


class WebsiteNotFoundError(Exception):
    pass


class WebsiteVersionNotFoundError(Exception):
    pass


class PageNotFoundError(Exception):
    pass


class WebsiteVersionImmutableError(Exception):
    pass


class NoPublishedVersionError(Exception):
    pass


class WebsiteService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    # --- Website / version retrieval ------------------------------------

    async def get_website(self, tenant_id: uuid.UUID) -> Website | None:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            return (
                await session.execute(select(Website).where(Website.tenant_id == tenant_id))
            ).scalar_one_or_none()

    async def get_website_by_id(self, tenant_id: uuid.UUID, website_id: uuid.UUID) -> Website:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            website = await session.get(Website, website_id)
            if website is None or website.tenant_id != tenant_id:
                raise WebsiteNotFoundError(f"Website {website_id} not found")
            return website

    async def get_version(self, tenant_id: uuid.UUID, version_id: uuid.UUID) -> WebsiteVersion:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            version = await session.get(WebsiteVersion, version_id)
            if version is None or version.tenant_id != tenant_id:
                raise WebsiteVersionNotFoundError(f"WebsiteVersion {version_id} not found")
            return version

    async def list_versions(self, tenant_id: uuid.UUID, website_id: uuid.UUID) -> list[WebsiteVersion]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            rows = (
                await session.execute(
                    select(WebsiteVersion)
                    .where(WebsiteVersion.tenant_id == tenant_id, WebsiteVersion.website_id == website_id)
                    .order_by(WebsiteVersion.version.desc())
                )
            ).scalars().all()
            return list(rows)

    async def get_current_draft(self, tenant_id: uuid.UUID, website_id: uuid.UUID) -> WebsiteVersion | None:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            return (
                await session.execute(
                    select(WebsiteVersion).where(
                        WebsiteVersion.tenant_id == tenant_id,
                        WebsiteVersion.website_id == website_id,
                        WebsiteVersion.status == WebsiteVersionStatus.DRAFT,
                    )
                )
            ).scalar_one_or_none()

    # --- creation --------------------------------------------------------

    async def create_website_with_specification(
        self,
        tenant_id: uuid.UUID,
        *,
        name: str,
        slug: str,
        blueprint_id: uuid.UUID | None,
        specification: WebsiteSpecification,
        provenance: dict[str, str] | None,
        created_by: uuid.UUID | None,
    ) -> tuple[Website, WebsiteVersion]:
        """Creates the tenant's Website (idempotent — reuses an existing
        one) plus a brand-new DRAFT version populated from an already-
        validated WebsiteSpecification. Never called with unvalidated
        input — app/api/v1/websites.py and
        app/services/website_generation_service.py both hand this an
        already-`WebsiteSpecification.model_validate`d object."""
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            website = (
                await session.execute(select(Website).where(Website.tenant_id == tenant_id))
            ).scalar_one_or_none()
            if website is None:
                website = Website(
                    tenant_id=tenant_id, name=name, slug=slug, blueprint_id=blueprint_id, created_by=created_by
                )
                session.add(website)
                await session.flush()

            max_version = (
                await session.execute(
                    select(WebsiteVersion.version)
                    .where(WebsiteVersion.tenant_id == tenant_id, WebsiteVersion.website_id == website.id)
                    .order_by(WebsiteVersion.version.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            next_version = (max_version or 0) + 1

            version = WebsiteVersion(
                tenant_id=tenant_id,
                website_id=website.id,
                version=next_version,
                status=WebsiteVersionStatus.DRAFT,
                theme=specification.theme.model_dump(),
                navigation=specification.navigation.model_dump(),
                seo_defaults=specification.seo_defaults.model_dump(),
                generation_provenance=provenance or {},
                created_by=created_by,
            )
            session.add(version)
            await session.flush()

            for page_index, page in enumerate(specification.pages):
                page_row = WebsitePage(
                    tenant_id=tenant_id,
                    website_version_id=version.id,
                    slug=page.slug,
                    title=page.title,
                    seo=page.seo.model_dump(),
                    order_index=page_index,
                )
                session.add(page_row)
                await session.flush()
                for section_index, section in enumerate(page.sections):
                    session.add(
                        WebsiteSection(
                            tenant_id=tenant_id,
                            page_id=page_row.id,
                            component_type=section.component_type.value,
                            props=section.props,
                            data_source=section.data_source.model_dump() if section.data_source else None,
                            order_index=section_index,
                        )
                    )

            session.add(
                AuditLog(
                    tenant_id=tenant_id,
                    actor_type=ActorType.USER if created_by else ActorType.SYSTEM,
                    actor_id=created_by,
                    action="website.version_created",
                    tool=None,
                    entity_type="website_version",
                    entity_id=version.id,
                    input_summary={"website_id": str(website.id), "version": next_version, "page_count": len(specification.pages)},
                    result="success",
                )
            )
            await session.commit()
            await session.refresh(website)
            await session.refresh(version)
            return website, version

    # --- load a version as an assembled, re-validated specification -----

    async def load_specification(self, tenant_id: uuid.UUID, version_id: uuid.UUID) -> WebsiteSpecification:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            version = await session.get(WebsiteVersion, version_id)
            if version is None or version.tenant_id != tenant_id:
                raise WebsiteVersionNotFoundError(f"WebsiteVersion {version_id} not found")
            pages = (
                await session.execute(
                    select(WebsitePage)
                    .where(WebsitePage.tenant_id == tenant_id, WebsitePage.website_version_id == version_id)
                    .order_by(WebsitePage.order_index)
                )
            ).scalars().all()
            page_specs = []
            for page in pages:
                sections = (
                    await session.execute(
                        select(WebsiteSection)
                        .where(WebsiteSection.tenant_id == tenant_id, WebsiteSection.page_id == page.id)
                        .order_by(WebsiteSection.order_index)
                    )
                ).scalars().all()
                section_specs = [
                    SectionSpec(
                        component_type=s.component_type,
                        props=s.props,
                        data_source=s.data_source,
                        order_index=s.order_index,
                    )
                    for s in sections
                ]
                page_specs.append(
                    PageSpec(
                        slug=page.slug,
                        title=page.title,
                        seo=SeoMetadata.model_validate(page.seo or {}),
                        sections=section_specs,
                    )
                )
            # Re-validating on every load is the "defense in depth" gate
            # design doc §14 calls for: a spec that was valid when written
            # must still be valid when read back, every time.
            return WebsiteSpecification(
                theme=ThemeTokens.model_validate(version.theme or {}),
                navigation=NavigationSpec.model_validate(version.navigation or {}),
                seo_defaults=SeoMetadata.model_validate(version.seo_defaults or {}),
                pages=page_specs,
            )

    # --- mutation (DRAFT-only) ------------------------------------------

    async def _require_draft(self, session, tenant_id: uuid.UUID, version_id: uuid.UUID) -> WebsiteVersion:
        version = await session.get(WebsiteVersion, version_id)
        if version is None or version.tenant_id != tenant_id:
            raise WebsiteVersionNotFoundError(f"WebsiteVersion {version_id} not found")
        if version.status != WebsiteVersionStatus.DRAFT:
            raise WebsiteVersionImmutableError(
                f"WebsiteVersion {version_id} is {version.status}, not DRAFT — cannot be mutated"
            )
        return version

    async def update_theme(
        self, tenant_id: uuid.UUID, version_id: uuid.UUID, theme: ThemeTokens, *, updated_by: uuid.UUID | None
    ) -> WebsiteVersion:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            version = await self._require_draft(session, tenant_id, version_id)
            version.theme = theme.model_dump()
            session.add(
                AuditLog(
                    tenant_id=tenant_id,
                    actor_type=ActorType.USER,
                    actor_id=updated_by,
                    action="website.version_updated",
                    entity_type="website_version",
                    entity_id=version.id,
                    input_summary={"field": "theme"},
                    result="success",
                )
            )
            await session.commit()
            await session.refresh(version)
            return version

    async def add_page(
        self,
        tenant_id: uuid.UUID,
        version_id: uuid.UUID,
        page: PageSpec,
        *,
        updated_by: uuid.UUID | None,
    ) -> WebsitePage:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            await self._require_draft(session, tenant_id, version_id)
            existing = (
                await session.execute(
                    select(WebsitePage).where(
                        WebsitePage.tenant_id == tenant_id,
                        WebsitePage.website_version_id == version_id,
                        WebsitePage.slug == page.slug,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise ValueError(f"Page slug {page.slug!r} already exists on this version")
            max_order = (
                await session.execute(
                    select(WebsitePage.order_index)
                    .where(WebsitePage.tenant_id == tenant_id, WebsitePage.website_version_id == version_id)
                    .order_by(WebsitePage.order_index.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            page_row = WebsitePage(
                tenant_id=tenant_id,
                website_version_id=version_id,
                slug=page.slug,
                title=page.title,
                seo=page.seo.model_dump(),
                order_index=(max_order or 0) + 1,
            )
            session.add(page_row)
            await session.flush()
            for section_index, section in enumerate(page.sections):
                session.add(
                    WebsiteSection(
                        tenant_id=tenant_id,
                        page_id=page_row.id,
                        component_type=section.component_type.value,
                        props=section.props,
                        data_source=section.data_source.model_dump() if section.data_source else None,
                        order_index=section_index,
                    )
                )
            session.add(
                AuditLog(
                    tenant_id=tenant_id,
                    actor_type=ActorType.USER,
                    actor_id=updated_by,
                    action="website.version_updated",
                    entity_type="website_page",
                    entity_id=page_row.id,
                    input_summary={"field": "page_added", "slug": page.slug},
                    result="success",
                )
            )
            await session.commit()
            await session.refresh(page_row)
            return page_row

    async def replace_page_sections(
        self,
        tenant_id: uuid.UUID,
        version_id: uuid.UUID,
        page_slug: str,
        sections: list[SectionSpec],
        *,
        updated_by: uuid.UUID | None,
    ) -> WebsitePage:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            await self._require_draft(session, tenant_id, version_id)
            page = (
                await session.execute(
                    select(WebsitePage).where(
                        WebsitePage.tenant_id == tenant_id,
                        WebsitePage.website_version_id == version_id,
                        WebsitePage.slug == page_slug,
                    )
                )
            ).scalar_one_or_none()
            if page is None:
                raise PageNotFoundError(f"Page {page_slug!r} not found on version {version_id}")

            existing_sections = (
                await session.execute(
                    select(WebsiteSection).where(
                        WebsiteSection.tenant_id == tenant_id, WebsiteSection.page_id == page.id
                    )
                )
            ).scalars().all()
            for row in existing_sections:
                await session.delete(row)
            await session.flush()
            for section_index, section in enumerate(sections):
                session.add(
                    WebsiteSection(
                        tenant_id=tenant_id,
                        page_id=page.id,
                        component_type=section.component_type.value,
                        props=section.props,
                        data_source=section.data_source.model_dump() if section.data_source else None,
                        order_index=section_index,
                    )
                )
            session.add(
                AuditLog(
                    tenant_id=tenant_id,
                    actor_type=ActorType.USER,
                    actor_id=updated_by,
                    action="website.version_updated",
                    entity_type="website_page",
                    entity_id=page.id,
                    input_summary={"field": "sections_replaced", "slug": page_slug, "count": len(sections)},
                    result="success",
                )
            )
            await session.commit()
            await session.refresh(page)
            return page

    # --- new-draft-from-published (editing a live site) ------------------

    async def create_draft_from_version(
        self, tenant_id: uuid.UUID, website_id: uuid.UUID, source_version_id: uuid.UUID, *, created_by: uuid.UUID | None
    ) -> WebsiteVersion:
        """Clones a (typically PUBLISHED) version's full specification into
        a brand-new DRAFT version — the mechanism behind design doc §2's
        "editing a published site creates a new version" rule. The source
        version itself is never touched."""
        spec = await self.load_specification(tenant_id, source_version_id)
        website = await self.get_website_by_id(tenant_id, website_id)
        _website, new_version = await self.create_website_with_specification(
            tenant_id,
            name=website.name,
            slug=website.slug,
            blueprint_id=website.blueprint_id,
            specification=spec,
            provenance=None,
            created_by=created_by,
        )
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            row = await session.get(WebsiteVersion, new_version.id)
            row.supersedes_id = source_version_id
            await session.commit()
            await session.refresh(row)
            return row

    # --- publish / unpublish ----------------------------------------------

    async def publish_version(
        self, tenant_id: uuid.UUID, website_id: uuid.UUID, version_id: uuid.UUID, *, published_by: uuid.UUID | None
    ) -> WebsiteVersion:
        # Defense in depth (design doc §14): re-validate the full assembled
        # specification one final time before flipping any DB state.
        try:
            await self.load_specification(tenant_id, version_id)
        except Exception as exc:  # noqa: BLE001
            async with self._session_factory() as session:
                await set_tenant_context(session, tenant_id)
                session.add(
                    AuditLog(
                        tenant_id=tenant_id,
                        actor_type=ActorType.USER,
                        actor_id=published_by,
                        action="website.publish_failed",
                        entity_type="website_version",
                        entity_id=version_id,
                        input_summary={"reason": str(exc)[:500]},
                        result="failure",
                    )
                )
                await session.commit()
            raise

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            website = await session.get(Website, website_id)
            if website is None or website.tenant_id != tenant_id:
                raise WebsiteNotFoundError(f"Website {website_id} not found")
            version = await session.get(WebsiteVersion, version_id)
            if version is None or version.tenant_id != tenant_id or version.website_id != website_id:
                raise WebsiteVersionNotFoundError(f"WebsiteVersion {version_id} not found")
            if version.status != WebsiteVersionStatus.DRAFT:
                raise WebsiteVersionImmutableError(
                    f"WebsiteVersion {version_id} is {version.status}, not DRAFT — cannot publish"
                )

            currently_published = (
                await session.execute(
                    select(WebsiteVersion).where(
                        WebsiteVersion.tenant_id == tenant_id,
                        WebsiteVersion.website_id == website_id,
                        WebsiteVersion.status == WebsiteVersionStatus.PUBLISHED,
                    )
                )
            ).scalar_one_or_none()
            if currently_published is not None:
                currently_published.status = WebsiteVersionStatus.SUPERSEDED
                # Flush the old version's SUPERSEDED transition before
                # flipping the new version to PUBLISHED — otherwise the
                # partial-unique "at most one PUBLISHED version" index can
                # see both rows as PUBLISHED simultaneously mid-flush and
                # reject the second UPDATE, even though the end state is
                # valid (verified against a real IntegrityError on SQLite;
                # the same ordering hazard applies to the Postgres partial
                # index, which is checked per-statement, not deferred).
                await session.flush()

            now = datetime.now(timezone.utc)
            version.status = WebsiteVersionStatus.PUBLISHED
            version.published_at = now
            version.published_by = published_by
            website.current_published_version_id = version.id

            session.add(
                AuditLog(
                    tenant_id=tenant_id,
                    actor_type=ActorType.USER,
                    actor_id=published_by,
                    action="website.published",
                    entity_type="website_version",
                    entity_id=version.id,
                    input_summary={"website_id": str(website_id), "version": version.version},
                    result="success",
                )
            )
            await session.commit()
            await session.refresh(version)
            return version

    async def unpublish(self, tenant_id: uuid.UUID, website_id: uuid.UUID, *, unpublished_by: uuid.UUID | None) -> Website:
        """Design doc §14's explicit decision: clears the "live" pointer
        only — the WebsiteVersion.status itself stays PUBLISHED (its
        history/immutability is preserved), it is simply no longer served
        as the current site."""
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            website = await session.get(Website, website_id)
            if website is None or website.tenant_id != tenant_id:
                raise WebsiteNotFoundError(f"Website {website_id} not found")
            if website.current_published_version_id is None:
                raise NoPublishedVersionError("Website has no published version to unpublish")
            website.current_published_version_id = None
            session.add(
                AuditLog(
                    tenant_id=tenant_id,
                    actor_type=ActorType.USER,
                    actor_id=unpublished_by,
                    action="website.unpublished",
                    entity_type="website",
                    entity_id=website.id,
                    input_summary={},
                    result="success",
                )
            )
            await session.commit()
            await session.refresh(website)
            return website

    async def get_published_specification(
        self, tenant_id: uuid.UUID, website_id: uuid.UUID
    ) -> WebsiteSpecification:
        website = await self.get_website_by_id(tenant_id, website_id)
        if website.current_published_version_id is None:
            raise NoPublishedVersionError(f"Website {website_id} has no published version")
        return await self.load_specification(tenant_id, website.current_published_version_id)
