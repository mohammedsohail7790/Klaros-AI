"""section 11: provider abstraction.

Business services (FinanceService, CRMService, ...) call these interfaces,
never a provider SDK directly. An adapter that lacks real credentials must
say so honestly via `get_status()` — it must never fabricate a successful
response.
"""

from abc import ABC
from dataclasses import dataclass
from enum import StrEnum


class ConnectionStatus(StrEnum):
    NOT_CONNECTED = "NOT_CONNECTED"
    CONNECTED = "CONNECTED"
    ERROR = "ERROR"


@dataclass
class IntegrationStatus:
    provider: str
    status: ConnectionStatus
    detail: str


class IntegrationProvider(ABC):
    provider_name: str

    def get_status(self) -> IntegrationStatus:
        raise NotImplementedError


class FinanceProvider(IntegrationProvider):
    pass


class CRMProvider(IntegrationProvider):
    pass


class OperationsProvider(IntegrationProvider):
    pass


class MarketingProvider(IntegrationProvider):
    pass


class CommunicationProvider(IntegrationProvider):
    pass


class CalendarProvider(IntegrationProvider):
    pass
