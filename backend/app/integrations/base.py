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

    async def check_status(self) -> IntegrationStatus:
        """Phase 12C: like get_status(), but permitted to make a real,
        cheap, read-only API call to verify the configured credential
        actually works — not just that an env var is non-empty. Defaults
        to get_status() for adapters that have no real client yet (still
        honest: env-var-presence is all they can report). Override this,
        not get_status(), when real verification becomes possible."""
        return self.get_status()


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


class AIProvider(IntegrationProvider):
    """Status-reporting marker for LLM providers — distinct from
    app/services/ai_provider.py's AIProvider ABC (that one is the actual
    enrich_brief() execution boundary); this one exists only so OpenAI/
    Anthropic appear in the same GET /api/v1/integrations list as every
    other provider."""

    pass


class ProcurementProvider(IntegrationProvider):
    """section 19: supplier/procurement integration — no supplier is
    connected in Phase 4; `MaterialService` only ever produces DRAFT
    purchase orders (see app/services/material_service.py)."""
