"""`GET /api/v1/tenant/modules`: which business modules (vertical extensions) the caller's own organisation has enabled.

The product is the same for every tenant; what a tenant sees and what its AI crew does is decided by its configuration. This is the one place the UI asks
"what has this business enabled?", so it never branches on a business name or a hard-coded tenant type. Two lists of keys, nothing personal, and no permission
beyond being signed in. A lookup failure yields an empty list (the UI then shows the core navigation, never hides it).
"""

from typing import Any

import structlog
from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, get_current_user
from app.db.session import async_session_maker
from app.services.vertical_extension_service import VerticalExtensionService

router = APIRouter(prefix="/tenant", tags=["tenant"])
logger = structlog.get_logger(__name__)


@router.get("/modules")
async def get_tenant_modules(current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    service = VerticalExtensionService(async_session_maker)
    try:
        enabled = await service.list_enabled_for_organization(current_user.tenant_id)
        wanted = {row.vertical_extension_id for row in enabled}
        modules = sorted(v.key for v in await service.list_verticals() if v.id in wanted)
    except Exception:  # noqa: BLE001 - never block the page on a catalogue hiccup
        logger.warning("tenant_modules_lookup_failed")
        modules = []
    return {"modules": modules}
