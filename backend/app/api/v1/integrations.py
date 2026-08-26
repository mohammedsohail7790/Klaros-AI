from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import CurrentUser, get_current_user
from app.integrations.adapters import ALL_ADAPTERS

router = APIRouter(prefix="/integrations", tags=["integrations"])


class IntegrationStatusResponse(BaseModel):
    provider: str
    status: str
    detail: str


@router.get("", response_model=list[IntegrationStatusResponse])
async def list_integration_status(
    current_user: CurrentUser = Depends(get_current_user),
) -> list[IntegrationStatusResponse]:
    del current_user
    statuses = [adapter_cls().get_status() for adapter_cls in ALL_ADAPTERS]
    return [
        IntegrationStatusResponse(provider=s.provider, status=s.status, detail=s.detail)
        for s in statuses
    ]
