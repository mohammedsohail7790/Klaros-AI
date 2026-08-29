"""Phase 6: paid-ads provider abstraction.

Distinct from the Phase 2 `app/integrations/adapters.py` status-check
stubs (`GoogleAdsAdapter`/`MetaAdsAdapter`, which only ever answer "are
credentials configured?"). These interfaces are what a real
`MarketingCampaignService` would call to actually sync a campaign or pull
performance — and every adapter here reports `NOT_CONNECTED` and refuses
to fabricate a response, same as every other Phase 2/5 provider. Business
logic never talks to an ads SDK directly.
"""

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal

from app.integrations.base import ConnectionStatus, IntegrationStatus


@dataclass
class AdsSyncResult:
    connected: bool
    provider: str
    detail: str
    spend: Decimal | None = None
    leads: int | None = None


class MarketingAdsProvider(ABC):
    provider_name: str

    @abstractmethod
    def get_status(self) -> IntegrationStatus: ...

    @abstractmethod
    async def sync_campaign_performance(self, tenant_id: uuid.UUID, external_campaign_id: str) -> AdsSyncResult: ...


class GoogleAdsProvider(MarketingAdsProvider):
    pass


class MetaAdsProvider(MarketingAdsProvider):
    pass


class YouTubeAdsProvider(MarketingAdsProvider):
    pass


class LocalServicesAdsProvider(MarketingAdsProvider):
    pass


class _NotConnectedAdsAdapter(MarketingAdsProvider):
    detail = "No credentials configured for this provider."

    def get_status(self) -> IntegrationStatus:
        return IntegrationStatus(self.provider_name, ConnectionStatus.NOT_CONNECTED, self.detail)

    async def sync_campaign_performance(self, tenant_id: uuid.UUID, external_campaign_id: str) -> AdsSyncResult:
        return AdsSyncResult(connected=False, provider=self.provider_name, detail=self.detail)


class NotConnectedGoogleAdsAdapter(_NotConnectedAdsAdapter, GoogleAdsProvider):
    provider_name = "google_ads"
    detail = "GOOGLE_ADS_DEVELOPER_TOKEN not configured — no campaign data has been synced."


class NotConnectedMetaAdsAdapter(_NotConnectedAdsAdapter, MetaAdsProvider):
    provider_name = "meta_ads"
    detail = "META_ADS_APP_ID not configured — no campaign data has been synced."


class NotConnectedYouTubeAdsAdapter(_NotConnectedAdsAdapter, YouTubeAdsProvider):
    provider_name = "youtube_ads"
    detail = "No YouTube Ads credentials configured — no campaign data has been synced."


class NotConnectedLocalServicesAdsAdapter(_NotConnectedAdsAdapter, LocalServicesAdsProvider):
    provider_name = "local_services_ads"
    detail = "No Local Services Ads credentials configured — no campaign data has been synced."
