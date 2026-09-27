"""Phase 3: process-wide RecommendationService singleton, mirroring
app/api/tool_deps_business_discovery.py's exact `@lru_cache` factory-
function pattern."""

from functools import lru_cache

from app.db.session import async_session_maker
from app.services.recommendation_service import RecommendationService


@lru_cache
def get_recommendation_service() -> RecommendationService:
    return RecommendationService(async_session_maker)
