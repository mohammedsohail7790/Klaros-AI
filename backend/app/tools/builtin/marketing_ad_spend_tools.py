"""marketing.sync_ad_spend: pulls the last few days of daily campaign cost from the business's connected Google Ads / Meta Ads account into Klaros's own campaign and spend
records. It reads from the provider and writes only internal data (idempotent, never a second row for the same campaign and day), so it is AUTO. With no ad account
connected it returns a clean `not_connected` result instead of failing."""

from pydantic import BaseModel, Field

from app.models.rbac import Permission
from app.services.ad_spend_sync_service import DEFAULT_DAYS, MAX_DAYS, AdSpendSyncService
from app.tools.base import ExecutionContext, Tool


class SyncAdSpendInput(BaseModel):
    days: int = Field(default=DEFAULT_DAYS, ge=1, le=MAX_DAYS, description="How many days back to pull, ending today.")


class SyncAdSpendOutput(BaseModel):
    status: str  # synced | partial | error | not_connected
    detail: str | None = None
    providers: list[dict]


class SyncAdSpend(Tool):
    name = "marketing.sync_ad_spend"
    description = (
        "Pull daily campaign cost for the last N days (default 7) from the connected Google Ads and/or Meta Ads account into campaign and spend records. Read-only on the ad "
        "platform; safe to repeat. Returns status not_connected when no ad account is connected."
    )
    input_schema = SyncAdSpendInput
    output_schema = SyncAdSpendOutput
    required_permission = Permission.MANAGE_CAMPAIGNS

    def __init__(self, service: AdSpendSyncService) -> None:
        self._service = service

    async def execute(self, input: SyncAdSpendInput, context: ExecutionContext) -> SyncAdSpendOutput:
        result = await self._service.sync(context.tenant_id, days=input.days)
        return SyncAdSpendOutput(status=result["status"], detail=result.get("detail"), providers=result["providers"])
