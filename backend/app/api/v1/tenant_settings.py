"""`/api/v1/tenant/settings/ai-processing`: which external AI providers the OWNER allows to process this business's own data.

This is a legal choice, not a technical one: for a consent-gated business (for example Medical Tourism) the platform default is that no external model sees its content.
The owner can allow a named provider for their business, but only by saying they have a lawful basis for it (for example a data-processing agreement with that provider).
Only the OWNER role can change it; every change is written to the audit log with who made it. Phone numbers and e-mail addresses are still scrubbed from what is sent.
"""

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, get_current_user
from app.db.session import async_session_maker, set_tenant_context
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.rbac import Role
from app.services.tenant_settings_service import AI_PROCESSING, TenantSettingsService

router = APIRouter(prefix="/tenant/settings", tags=["tenant"])
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{1,39}$")


class AIProcessingRequest(BaseModel):
    external_providers: list[str] = Field(default_factory=list, max_length=5)
    # The owner confirms they have a lawful basis to let these providers process this business's data. Required whenever the list is not empty.
    acknowledged: bool = False


def _owner_only(current_user: CurrentUser) -> None:
    if current_user.role != Role.OWNER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the owner can change which AI providers may process this business's data.")


@router.get("/ai-processing")
async def get_ai_processing(current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    value = await TenantSettingsService(async_session_maker).get(current_user.tenant_id, AI_PROCESSING)
    return {"external_providers": list(value.get("external_providers") or []), "acknowledged_by": value.get("acknowledged_by"), "acknowledged_at": value.get("acknowledged_at")}


@router.put("/ai-processing")
async def put_ai_processing(body: AIProcessingRequest, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    _owner_only(current_user)
    providers = sorted({p.strip().lower() for p in body.external_providers if p.strip()})
    bad = [p for p in providers if not _NAME.match(p)]
    if bad:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Not a valid provider name: {', '.join(bad)}")
    if providers and not body.acknowledged:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Confirm that you have a lawful basis (for example a data-processing agreement) before allowing an external AI provider.",
        )
    from datetime import datetime, timezone

    value: dict[str, Any] = {"external_providers": providers}
    if providers:
        value |= {"acknowledged_by": str(current_user.id), "acknowledged_at": datetime.now(timezone.utc).isoformat()}
    await TenantSettingsService(async_session_maker).set(current_user.tenant_id, AI_PROCESSING, value, updated_by=current_user.id)
    async with async_session_maker() as session:
        await set_tenant_context(session, current_user.tenant_id)
        session.add(
            AuditLog(
                tenant_id=current_user.tenant_id, actor_type=ActorType.USER, actor_id=current_user.id, action="tenant.ai_processing.update",
                entity_type="tenant_setting", input_summary={"external_providers": providers}, result="success",
            )
        )
        await session.commit()
    return {"external_providers": providers, **{k: value[k] for k in ("acknowledged_by", "acknowledged_at") if k in value}}
