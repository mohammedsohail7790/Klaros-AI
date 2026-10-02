"""Which `WorkforceIntegration` this Klaros deployment uses.

Today: the `PendingWorkforceIntegration` placeholder, which can only report
that no external AI workforce is connected. When a real adapter against
Halla's documented API is built, it is registered here — nothing else in
Klaros changes, because every consumer depends on the contract only.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.integrations.workforce.contract import (
    WorkforceAgentSpec,
    WorkforceIntegration,
    WorkforceStatus,
    WorkforceStatusReport,
)


class WorkforceNotConnectedError(Exception):
    """Raised by mutating operations on the pending adapter — Klaros will
    not pretend to configure or deploy anything on an external platform it
    has no adapter for."""


class PendingWorkforceIntegration(WorkforceIntegration):
    provider = "halla"

    _MESSAGE = (
        "No AI workforce is connected. Halla AI is a separate platform; Klaros defines the "
        "integration contract but the Halla adapter has not been built yet, so nothing can be "
        "configured or deployed from here."
    )

    async def get_status(self, tenant_id: UUID) -> WorkforceStatusReport:
        return WorkforceStatusReport(
            status=WorkforceStatus.NOT_CONNECTED,
            adapter_implemented=False,
            provider=self.provider,
            message=self._MESSAGE,
        )

    async def health_check(self, tenant_id: UUID) -> WorkforceStatusReport:
        return await self.get_status(tenant_id)

    async def configure(self, tenant_id: UUID, config: dict[str, Any]) -> WorkforceStatusReport:
        raise WorkforceNotConnectedError(self._MESSAGE)

    async def deploy_agent(self, tenant_id: UUID, spec: WorkforceAgentSpec) -> dict[str, Any]:
        raise WorkforceNotConnectedError(self._MESSAGE)

    async def get_agent(self, tenant_id: UUID) -> dict[str, Any] | None:
        return None

    async def update_agent(self, tenant_id: UUID, spec: WorkforceAgentSpec) -> dict[str, Any]:
        raise WorkforceNotConnectedError(self._MESSAGE)


_integration: WorkforceIntegration = PendingWorkforceIntegration()


def get_workforce_integration() -> WorkforceIntegration:
    return _integration
