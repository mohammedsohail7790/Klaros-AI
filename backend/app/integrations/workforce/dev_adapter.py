"""Development simulator for the AI-workforce boundary.

NOT Halla and NOT a connection. It exists so the Klaros side of the loop (context pack
out, interaction events in, lead state, UI) can be exercised in development and tests
without an external platform. It always reports NOT_CONNECTED with mode="development" and
`adapter_implemented=False`, so nothing in Klaros can ever show it as connected, and every
screen keeps saying "Integration required". Enabled only with WORKFORCE_ADAPTER=dev.
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

_MESSAGE = (
    "Development simulator — not a live Halla connection. Klaros can exercise its side of the "
    "loop here, but no real conversations happen."
)


class DevWorkforceIntegration(WorkforceIntegration):
    provider = "halla"

    def __init__(self) -> None:
        self._specs: dict[UUID, WorkforceAgentSpec] = {}

    async def get_status(self, tenant_id: UUID) -> WorkforceStatusReport:
        return WorkforceStatusReport(
            status=WorkforceStatus.NOT_CONNECTED, adapter_implemented=False, provider=self.provider,
            message=_MESSAGE, mode="development",
        )

    async def health_check(self, tenant_id: UUID) -> WorkforceStatusReport:
        return await self.get_status(tenant_id)

    async def configure(self, tenant_id: UUID, config: dict[str, Any]) -> WorkforceStatusReport:
        return await self.get_status(tenant_id)

    async def deploy_agent(self, tenant_id: UUID, spec: WorkforceAgentSpec) -> dict[str, Any]:
        # Recorded so tests can inspect what Klaros would have sent; nothing is deployed anywhere.
        self._specs[tenant_id] = spec
        return {"mode": "development", "deployed": False, "note": _MESSAGE}

    async def get_agent(self, tenant_id: UUID) -> dict[str, Any] | None:
        return None

    async def update_agent(self, tenant_id: UUID, spec: WorkforceAgentSpec) -> dict[str, Any]:
        return await self.deploy_agent(tenant_id, spec)

    def last_spec(self, tenant_id: UUID) -> WorkforceAgentSpec | None:
        return self._specs.get(tenant_id)
