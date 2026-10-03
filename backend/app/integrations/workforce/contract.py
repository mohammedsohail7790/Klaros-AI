"""The Klaros-side contract for an external AI workforce platform.

This is an interface, not an integration. It mirrors the shape of the other
adapters in `app/integrations` (a small ABC plus plain dataclasses) and
deliberately specifies NO transport: no URLs, no auth scheme, no payload
format. Those belong to the external platform's own API contract, which is
not available inside Klaros. A concrete adapter (a future
`HallaWorkforceIntegration`, built against Halla's real, documented API)
must implement every method below and return truthful status — in
particular `CONNECTED` may only be returned after a real health check
against the live external system has succeeded.

Status vocabulary (never anything softer than the truth):

  NOT_CONNECTED           no adapter is wired / the tenant has not connected one
  CONFIGURATION_REQUIRED  an adapter exists but needs tenant configuration
  CONNECTED               a live health check succeeded just now
  ERROR                   an adapter exists and its last health check failed
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID


class WorkforceNotConnectedError(Exception):
    """Raised by an operation that needs a live workforce connection when there is none. Klaros never
    pretends to configure, sync or call anything on an external platform it is not connected to."""


class WorkforceUnavailableError(Exception):
    """The workforce platform could not be reached or answered with an error. `retryable` says whether
    trying again later can help (network/5xx/429) or not (a 4xx that will not change)."""

    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class WorkforceStatus(StrEnum):
    # NOT_CONNECTED == "not configured": no connection (or no credential) exists for this tenant.
    NOT_CONNECTED = "NOT_CONNECTED"
    CONNECTING = "CONNECTING"
    # Needs setup, or the connection works but disagrees with what Klaros expects (e.g. tenant mismatch).
    CONFIGURATION_REQUIRED = "CONFIGURATION_REQUIRED"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"
    CONNECTED = "CONNECTED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class WorkforceCapability:
    key: str
    label: str
    description: str


# What an AI workforce is *expected* to be able to do for a business. This
# is the vocabulary the Business Map / Requirements use to say "this part of
# your business could be run by your AI workforce" — it is a statement about
# the contract, never about what is currently deployed.
WORKFORCE_CAPABILITIES: tuple[WorkforceCapability, ...] = (
    WorkforceCapability("voice", "Voice", "Natural spoken conversations with customers."),
    WorkforceCapability("incoming_calls", "Incoming calls", "Answer inbound calls."),
    WorkforceCapability("outgoing_calls", "Outgoing calls", "Place outbound calls."),
    WorkforceCapability("lead_qualification", "Lead qualification", "Qualify enquiries before they reach a person."),
    WorkforceCapability("appointment_booking", "Appointment booking", "Book consultations or appointments."),
    WorkforceCapability("customer_support", "Customer support", "Answer customer questions."),
    WorkforceCapability("outbound_communication", "Outbound communication", "Follow up and re-engage customers."),
)


@dataclass(frozen=True)
class WorkforceAgentSpec:
    """What Klaros asks the workforce platform to deploy. Derived from the
    business blueprint — the platform decides how to realise it."""

    tenant_id: UUID
    business_name: str
    capabilities: tuple[str, ...]
    business_summary: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkforceStatusReport:
    status: WorkforceStatus
    # True only when a concrete adapter for the external platform exists in
    # this deployment of Klaros.
    adapter_implemented: bool
    provider: str
    message: str
    capabilities: tuple[WorkforceCapability, ...] = WORKFORCE_CAPABILITIES
    agent_id: str | None = None
    checked_at: str | None = None
    # "none" (nothing wired), "development" (a local simulator — NOT a live connection),
    # "live" (a real adapter against the external platform).
    mode: str = "none"


@dataclass(frozen=True)
class WorkforceAgent:
    """One AI agent of the workforce platform, as Klaros shows it. Klaros does not manage agents."""

    id: str
    name: str
    role: str | None = None
    status: str | None = None
    available: bool | None = None


@dataclass(frozen=True)
class LeadSyncResult:
    external_id: str
    created: bool


@dataclass(frozen=True)
class OutboundCallResult:
    call_id: str | None


class WorkforceIntegration(ABC):
    """The adapter contract. Every method is tenant-scoped; adapters must
    never hold or return another tenant's data or credentials."""

    provider: str

    @abstractmethod
    async def get_status(self, tenant_id: UUID) -> WorkforceStatusReport: ...

    @abstractmethod
    async def configure(self, tenant_id: UUID, config: dict[str, Any]) -> WorkforceStatusReport: ...

    @abstractmethod
    async def deploy_agent(self, tenant_id: UUID, spec: WorkforceAgentSpec) -> dict[str, Any]: ...

    @abstractmethod
    async def get_agent(self, tenant_id: UUID) -> dict[str, Any] | None: ...

    @abstractmethod
    async def update_agent(self, tenant_id: UUID, spec: WorkforceAgentSpec) -> dict[str, Any]: ...

    @abstractmethod
    async def health_check(self, tenant_id: UUID) -> WorkforceStatusReport: ...

    # --- Operations a LIVE adapter implements. The defaults refuse honestly, so the pending adapter and the
    # development simulator can never appear to do any of this. ---

    async def get_workforce(self, tenant_id: UUID) -> dict[str, Any]:
        raise WorkforceNotConnectedError("No AI workforce is connected.")

    async def configure_workforce(self, tenant_id: UUID, config: dict[str, Any]) -> dict[str, Any]:
        raise WorkforceNotConnectedError("No AI workforce is connected.")

    async def list_agents(self, tenant_id: UUID) -> list[WorkforceAgent]:
        raise WorkforceNotConnectedError("No AI workforce is connected.")

    async def sync_lead(self, tenant_id: UUID, lead: dict[str, Any], *, external_id: str | None) -> LeadSyncResult:
        raise WorkforceNotConnectedError("No AI workforce is connected.")

    async def initiate_outbound_call(
        self, tenant_id: UUID, *, klaros_lead_id: UUID, to_number: str, reason: str, opening_context: str | None
    ) -> OutboundCallResult:
        raise WorkforceNotConnectedError("No AI workforce is connected.")
