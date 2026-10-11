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
    WorkforceNotConnectedError,
    WorkforceIntegration,
    WorkforceStatus,
    WorkforceStatusReport,
)


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


_pending: WorkforceIntegration = PendingWorkforceIntegration()
_dev: WorkforceIntegration | None = None


def get_workforce_integration() -> WorkforceIntegration:
    """Pending by default. `WORKFORCE_ADAPTER=dev` swaps in the development simulator —
    still never CONNECTED. A real Halla adapter would be selected here."""
    global _dev
    from app.core.config import get_settings

    if get_settings().WORKFORCE_ADAPTER == "halla":
        return _halla()
    if _dev_selected():
        if _dev is None:
            from app.integrations.workforce.dev_adapter import DevWorkforceIntegration

            _dev = DevWorkforceIntegration()
        return _dev
    return _pending


_halla_instance: WorkforceIntegration | None = None


def _halla() -> WorkforceIntegration:
    global _halla_instance
    if _halla_instance is None:
        from app.api.tool_deps_integrations import get_integration_connection_service
        from app.integrations.workforce.halla_adapter import HallaWorkforceIntegration

        _halla_instance = HallaWorkforceIntegration(get_integration_connection_service())
    return _halla_instance


def halla_enabled() -> bool:
    from app.core.config import get_settings

    return get_settings().WORKFORCE_ADAPTER == "halla"


def _dev_selected() -> bool:
    from app.core.config import get_settings

    settings = get_settings()
    # The simulator can never be switched on in production, whatever the setting says.
    return settings.WORKFORCE_ADAPTER == "dev" and settings.ENV.lower() not in ("production", "prod")


def dev_simulator_enabled() -> bool:
    return _dev_selected()
