"""Phase 6: outbound list-building/enrichment provider abstraction. Every
adapter reports NOT_CONNECTED and returns nothing — Klaros never fabricates
a contact list, an enrichment result, or a permit-data record."""

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.integrations.base import ConnectionStatus, IntegrationStatus


@dataclass
class EnrichmentResult:
    connected: bool
    provider: str
    detail: str


class LeadListProvider(ABC):
    provider_name: str

    @abstractmethod
    def get_status(self) -> IntegrationStatus: ...


class ContactEnrichmentProvider(ABC):
    provider_name: str

    @abstractmethod
    def get_status(self) -> IntegrationStatus: ...

    @abstractmethod
    async def enrich(self, tenant_id: uuid.UUID, contact_id: uuid.UUID) -> EnrichmentResult: ...


class OutboundProvider(ABC):
    provider_name: str

    @abstractmethod
    def get_status(self) -> IntegrationStatus: ...


class PermitDataProvider(ABC):
    provider_name: str

    @abstractmethod
    def get_status(self) -> IntegrationStatus: ...


class NotConnectedClayAdapter(LeadListProvider, ContactEnrichmentProvider):
    provider_name = "clay"

    def get_status(self) -> IntegrationStatus:
        return IntegrationStatus(self.provider_name, ConnectionStatus.NOT_CONNECTED, "No Clay API key configured.")

    async def enrich(self, tenant_id: uuid.UUID, contact_id: uuid.UUID) -> EnrichmentResult:
        return EnrichmentResult(connected=False, provider=self.provider_name, detail="No Clay API key configured.")


class NotConnectedApolloAdapter(LeadListProvider, ContactEnrichmentProvider):
    provider_name = "apollo"

    def get_status(self) -> IntegrationStatus:
        return IntegrationStatus(self.provider_name, ConnectionStatus.NOT_CONNECTED, "No Apollo API key configured.")

    async def enrich(self, tenant_id: uuid.UUID, contact_id: uuid.UUID) -> EnrichmentResult:
        return EnrichmentResult(connected=False, provider=self.provider_name, detail="No Apollo API key configured.")


class NotConnectedInstantlyAdapter(OutboundProvider):
    provider_name = "instantly"

    def get_status(self) -> IntegrationStatus:
        return IntegrationStatus(
            self.provider_name, ConnectionStatus.NOT_CONNECTED, "No Instantly API key configured."
        )


class NotConnectedPermitDataAdapter(PermitDataProvider):
    provider_name = "permit_data"

    def get_status(self) -> IntegrationStatus:
        return IntegrationStatus(
            self.provider_name, ConnectionStatus.NOT_CONNECTED, "No permit-data provider configured."
        )
