"""Phase 11: process-wide Website Builder service singletons, mirroring
app/api/tool_deps_recommendations.py's exact `@lru_cache` factory-function
pattern."""

from functools import lru_cache

from app.db.session import async_session_maker
from app.services.website_generation_service import WebsiteGenerationService
from app.services.website_service import WebsiteService


@lru_cache
def get_website_service() -> WebsiteService:
    return WebsiteService(async_session_maker)


@lru_cache
def get_website_generation_service() -> WebsiteGenerationService:
    return WebsiteGenerationService(async_session_maker)
