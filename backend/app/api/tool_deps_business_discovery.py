"""Phase 2: process-wide service singletons for Business Discovery /
Business Blueprint, mirroring app/api/tool_deps_integrations.py's exact
`@lru_cache` factory-function pattern."""

from functools import lru_cache

from app.db.session import async_session_maker
from app.services.ai_provider import get_ai_provider
from app.services.business_blueprint_service import BusinessBlueprintService
from app.services.business_discovery_service import BusinessDiscoveryService
from app.services.discovery_extraction_service import DiscoveryExtractionService


@lru_cache
def get_business_blueprint_service() -> BusinessBlueprintService:
    return BusinessBlueprintService(async_session_maker)


@lru_cache
def get_discovery_extraction_service() -> DiscoveryExtractionService:
    return DiscoveryExtractionService(async_session_maker, get_ai_provider())


@lru_cache
def get_business_discovery_service() -> BusinessDiscoveryService:
    return BusinessDiscoveryService(
        async_session_maker, get_business_blueprint_service(), get_discovery_extraction_service()
    )
