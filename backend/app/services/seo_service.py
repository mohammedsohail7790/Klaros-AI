"""section: Local SEO Page Engine. Pages are always created DRAFT / AI
GENERATED; publishing is a separate, explicit action (not gated through
the generic ApprovalRequest system here since it's a low-risk, reversible
content edit — unlike Finance/marketing-content publication — but it is
never automatic on generation).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.models.actor import ActorType
from app.models.marketing import SEOKeyword, SEOOpportunity, SEOPage, SEOPageStatus
from app.services.ai_boundary import bound_ai_provider, bound_embedding_provider
from app.services.ai_content_service import (
    generate_seo_page_draft,
    generate_seo_page_draft_via_ai,
    is_llm_connected,
)
from app.services.ai_invocation_log_service import record_ai_invocation
from app.services.ai_provider import AIProvider
from app.services.company_memory_service import CompanyMemoryService, format_context_as_text


class SEOPageNotFoundError(Exception):
    pass


class SEOService:
    def __init__(self, session_factory: async_sessionmaker, ai_provider: AIProvider | None = None) -> None:
        self._session_factory = session_factory
        # Phase 16: real Company Memory integration + the real LLM SEO
        # path (see generate_page_draft below). `ai_provider` is
        # optional/keyword so the existing 1-arg call site (factory.py)
        # and any test constructing SEOService(session_factory) alone are
        # unaffected — defaults to the same real get_ai_provider()
        # factory Morning Brief/Qualification/Marketing Content use.
        if ai_provider is None:
            from app.services.ai_provider import get_ai_provider

            ai_provider = get_ai_provider()
        self._ai_provider = ai_provider
        self._memory = CompanyMemoryService(session_factory)

    async def generate_page_draft(
        self, tenant_id: uuid.UUID, *, service: str, location: str,
        actor_type: ActorType = ActorType.USER, actor_id: uuid.UUID | None = None,
        correlation_id: uuid.UUID | None = None,
    ) -> SEOPage:
        """Phase 16: when `is_llm_connected()`, calls the real
        `generate_seo_page_draft_via_ai` (governed `AIProvider.
        generate_structured()` boundary, real `AIInvocationLog` row,
        Company Memory context fenced as DATA) and falls back to the
        unchanged deterministic template only if that call fails — never
        a silent downgrade presented as the real thing. When no provider
        is configured (the only case in this sandbox), behavior is
        byte-for-byte unchanged from before this phase."""
        ai_draft = None
        if is_llm_connected():
            company_memory = format_context_as_text(await self._memory.get_context(tenant_id))
            ai_draft, outcome = await generate_seo_page_draft_via_ai(
                service, location, bound_ai_provider(self._ai_provider, self._session_factory, tenant_id, "seo_page"), company_memory=company_memory,
            )
            await record_ai_invocation(
                self._session_factory, tenant_id=tenant_id, actor_type=actor_type, actor_id=actor_id,
                operation="seo_page_generation", outcome=outcome, correlation_id=correlation_id,
                input_metadata={
                    "service": service, "location": location, "company_memory_used": company_memory is not None,
                },
            )

        if ai_draft is not None:
            title, meta_title, meta_description, h1, body_draft = (
                ai_draft.title, ai_draft.meta_title, ai_draft.meta_description, ai_draft.h1, ai_draft.body_draft,
            )
        else:
            # Either no provider configured, or the real call failed —
            # both fall back to the deterministic template honestly
            # (never silently presented as an LLM result).
            draft = generate_seo_page_draft(service, location)
            title, meta_title, meta_description, h1, body_draft = (
                draft["title"], draft["meta_title"], draft["meta_description"], draft["h1"], draft["body_draft"],
            )

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            page = SEOPage(
                tenant_id=tenant_id, service=service, location=location,
                url_slug=f"{service}-{location}".lower().replace(" ", "-").replace(",", ""),
                title=title, meta_title=meta_title, meta_description=meta_description,
                h1=h1, body_draft=body_draft, status=SEOPageStatus.DRAFT, ai_generated=True,
            )
            session.add(page)
            await session.commit()
            await session.refresh(page)
        return page

    async def publish_page(self, tenant_id: uuid.UUID, page_id: uuid.UUID) -> SEOPage:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
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
            await set_tenant_context(session, tenant_id)
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
            await set_tenant_context(session, tenant_id)
            row = SEOOpportunity(tenant_id=tenant_id, service=service, location=location, rationale=rationale, priority=priority)
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return row
