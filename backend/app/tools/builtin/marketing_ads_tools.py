"""section 1: Paid Acquisition. All ads-provider tools report NOT_CONNECTED
honestly — see app/marketing_ads/base.py. No fake campaign data is ever
returned."""

from pydantic import BaseModel

from app.marketing_ads.base import (
    NotConnectedGoogleAdsAdapter,
    NotConnectedLocalServicesAdsAdapter,
    NotConnectedMetaAdsAdapter,
    NotConnectedYouTubeAdsAdapter,
)
from app.models.rbac import Permission
from app.tools.base import ExecutionContext, Tool


class EmptyInput(BaseModel):
    pass


class AdsProviderStatusOutput(BaseModel):
    providers: list[dict]


class GetAdsProviderStatus(Tool):
    name = "marketing.get_ads_provider_status"
    description = "Report the real connection status of every paid-ads provider (never fabricated)."
    input_schema = EmptyInput
    output_schema = AdsProviderStatusOutput
    required_permission = Permission.READ_MARKETING

    _ADAPTERS = [
        NotConnectedGoogleAdsAdapter(),
        NotConnectedMetaAdsAdapter(),
        NotConnectedYouTubeAdsAdapter(),
        NotConnectedLocalServicesAdsAdapter(),
    ]

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> AdsProviderStatusOutput:
        statuses = [
            {"provider": s.provider, "status": s.status, "detail": s.detail}
            for s in (a.get_status() for a in self._ADAPTERS)
        ]
        return AdsProviderStatusOutput(providers=statuses)
