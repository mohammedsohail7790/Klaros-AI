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


class WorkforceStatus(StrEnum):
    NOT_CONNECTED = "NOT_CONNECTED"
    CONFIGURATION_REQUIRED = "CONFIGURATION_REQUIRED"
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
