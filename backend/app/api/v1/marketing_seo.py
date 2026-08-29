from typing import Any
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.models.marketing import LocalListing, LocalReview, SEOKeyword, SEOPage
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/marketing/seo", tags=["marketing-seo"])


@router.get("/pages")
async def list_pages(current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    rows = (await db.execute(select(SEOPage).where(SEOPage.tenant_id == current_user.tenant_id))).scalars().all()
    return {
        "pages": [
            {
                "id": str(p.id), "service": p.service, "location": p.location, "status": p.status,
                "title": p.title, "url_slug": p.url_slug, "ai_generated": p.ai_generated,
            }
            for p in rows
        ]
    }


@router.get("/pages/{page_id}")
async def get_page(page_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    page = await db.get(SEOPage, page_id)
    if page is None or page.tenant_id != current_user.tenant_id:
        from fastapi import HTTPException, status

        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SEO page not found")
    return {
        "id": str(page.id), "service": page.service, "location": page.location, "status": page.status,
        "title": page.title, "meta_title": page.meta_title, "meta_description": page.meta_description,
        "h1": page.h1, "body_draft": page.body_draft, "url_slug": page.url_slug, "ai_generated": page.ai_generated,
    }


@router.post("/pages/generate")
async def generate_page(body: dict, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    try:
        output = await registry.execute("marketing.generate_seo_page_draft", body, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/pages/{page_id}/publish")
async def publish_page(page_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    try:
        output = await registry.execute("marketing.publish_seo_page", {"page_id": str(page_id)}, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("/keywords")
async def list_keywords(current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    rows = (await db.execute(select(SEOKeyword).where(SEOKeyword.tenant_id == current_user.tenant_id))).scalars().all()
    return {
        "keywords": [
            {
                "id": str(k.id), "keyword": k.keyword, "target_location": k.target_location,
                "search_volume": k.search_volume, "current_ranking": k.current_ranking,
                "page_id": str(k.page_id) if k.page_id else None,
            }
            for k in rows
        ]
    }


@router.post("/keywords")
async def record_keyword(body: dict, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    try:
        output = await registry.execute("marketing.record_seo_keyword", body, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/opportunities")
async def create_opportunity(body: dict, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    try:
        output = await registry.execute("marketing.create_seo_opportunity", body, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("/local/listings")
async def list_listings(current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    rows = (await db.execute(select(LocalListing).where(LocalListing.tenant_id == current_user.tenant_id))).scalars().all()
    return {
        "listings": [
            {"id": str(l.id), "business_name": l.business_name, "city": l.city, "state": l.state, "provider": l.provider}
            for l in rows
        ]
    }


@router.post("/local/listings")
async def create_listing(body: dict, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    try:
        output = await registry.execute("marketing.create_local_listing", body, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("/local/reviews")
async def list_reviews(listing_id: uuid.UUID | None = None, current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    query = select(LocalReview).where(LocalReview.tenant_id == current_user.tenant_id)
    if listing_id:
        query = query.where(LocalReview.listing_id == listing_id)
    rows = (await db.execute(query)).scalars().all()
    return {
        "reviews": [
            {"id": str(r.id), "listing_id": str(r.listing_id), "rating": r.rating, "author": r.author, "body": r.body, "responded": r.responded}
            for r in rows
        ]
    }


@router.post("/local/reviews")
async def record_review(body: dict, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    try:
        output = await registry.execute("marketing.record_local_review", body, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/local/reviews/{review_id}/respond")
async def respond_to_review(review_id: uuid.UUID, response_text: str, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "marketing.respond_to_review", {"review_id": str(review_id), "response_text": response_text}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")
