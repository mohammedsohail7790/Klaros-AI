"""Phase 13: process-wide BusinessJourneyService singleton, mirroring
app/api/tool_deps_business_discovery.py / app/api/tool_deps_recommendations.py's
exact `@lru_cache` factory-function pattern."""

from functools import lru_cache

from app.api.tool_deps_business_discovery import (
    get_business_blueprint_service,
    get_business_discovery_service,
)
from app.api.tool_deps_recommendations import get_recommendation_service
from app.db.session import async_session_maker
from app.services.business_journey_service import BusinessJourneyService


@lru_cache
def get_business_journey_service() -> BusinessJourneyService:
    return BusinessJourneyService(
        async_session_maker,
        get_business_discovery_service(),
        get_business_blueprint_service(),
        get_recommendation_service(),
    )
