"""section: Local SEO Page Engine. Pages are always created DRAFT / AI
GENERATED; publishing is a separate, explicit action (not gated through
the generic ApprovalRequest system here since it's a low-risk, reversible
content edit — unlike Finance/marketing-content publication — but it is
never automatic on generation).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.marketing import SEOKeyword, SEOOpportunity, SEOPage, SEOPageStatus
from app.services.ai_content_service import generate_seo_page_draft


class SEOPageNotFoundError(Exception):
    pass


class SEOService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def generate_page_draft(self, tenant_id: uuid.UUID, *, service: str, location: str) -> SEOPage:
        draft = generate_seo_page_draft(service, location)
        async with self._session_factory() as session:
            page = SEOPage(
                tenant_id=tenant_id, service=service, location=location,
                url_slug=f"{service}-{location}".lower().replace(" ", "-").replace(",", ""),
                title=draft["title"], meta_title=draft["meta_title"], meta_description=draft["meta_description"],
                h1=draft["h1"], body_draft=draft["body_draft"], status=SEOPageStatus.DRAFT, ai_generated=True,
            )
            session.add(page)
            await session.commit()
            await session.refresh(page)
        return page

    async def publish_page(self, tenant_id: uuid.UUID, page_id: uuid.UUID) -> SEOPage:
        async with self._session_factory() as session:
            page = await session.get(SEOPage, page_id)
            if page is None or page.tenant_id != tenant_id:
                raise SEOPageNotFoundError("SEO page not found")
            page.status = SEOPageStatus.PUBLISHED
            await session.commit()
            await session.refresh(page)
        return page

    async def record_keyword(
        self, tenant_id: uuid.UUID, *, keyword: str, target_location: str | None, page_id: uuid.UUID | None,
        search_volume: int | None = None, current_ranking: int | None = None,
    ) -> SEOKeyword:
        async with self._session_factory() as session:
            row = SEOKeyword(
                tenant_id=tenant_id, keyword=keyword, target_location=target_location, page_id=page_id,
                search_volume=search_volume, current_ranking=current_ranking,
                ranking_observed_at=datetime.now(timezone.utc) if current_ranking is not None else None,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return row

    async def create_opportunity(
        self, tenant_id: uuid.UUID, *, service: str, location: str, rationale: str | None, priority: str = "MEDIUM",
    ) -> SEOOpportunity:
        async with self._session_factory() as session:
            row = SEOOpportunity(tenant_id=tenant_id, service=service, location=location, rationale=rationale, priority=priority)
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return row
