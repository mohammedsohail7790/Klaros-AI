"""Business Builder: process-wide BusinessBuilderService singleton, mirroring
app/api/tool_deps_business_journey.py's `@lru_cache` factory pattern."""

from functools import lru_cache

from app.api.tool_deps_business_discovery import get_business_blueprint_service
from app.api.tool_deps_recommendations import get_recommendation_service
from app.api.tool_deps_websites import get_website_service
from app.db.session import async_session_maker
from app.services.business_builder_service import BusinessBuilderService
from app.services.business_operations_service import BusinessOperationsService
from app.services.business_workforce_service import BusinessWorkforceService  # noqa: F401
from app.services.halla_integration_service import HallaIntegrationService  # noqa: F401
from app.services.vertical_extension_service import VerticalExtensionService


@lru_cache
def get_business_builder_service() -> BusinessBuilderService:
    return BusinessBuilderService(
        async_session_maker,
        get_business_blueprint_service(),
        get_recommendation_service(),
        get_website_service(),
        VerticalExtensionService(async_session_maker),
    )


@lru_cache
def get_business_operations_service() -> "BusinessOperationsService":
    from app.services.business_operations_service import BusinessOperationsService

    return BusinessOperationsService(async_session_maker, VerticalExtensionService(async_session_maker))


@lru_cache
def get_business_workforce_service() -> "BusinessWorkforceService":
    from app.services.business_workforce_service import BusinessWorkforceService

    return BusinessWorkforceService(async_session_maker, VerticalExtensionService(async_session_maker))


@lru_cache
def get_halla_integration_service() -> "HallaIntegrationService":
    from app.api.tool_deps_integrations import get_integration_connection_service
    from app.services.halla_integration_service import HallaIntegrationService

    return HallaIntegrationService(async_session_maker, get_integration_connection_service())
