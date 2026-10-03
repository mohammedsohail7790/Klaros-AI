"""The real Halla adapter: `WorkforceIntegration` implemented against Halla's Klaros contract.

Everything outside this module depends on the contract only and never sees Halla's HTTP details.
Per tenant, the credential lives in the existing `IntegrationConnection` row, encrypted with the
existing credential store (`provider="halla"`):

    encrypted blob  {api_key, signing_secret, halla_tenant_id}      <- never leaves the server
    external_account_id  halla_tenant_id                            <- the Klaros tenant <-> Halla tenant map
    status / last_verified_at / last_error                          <- from a REAL health request

`CONNECTED` is only ever reported from a real health request: a stored CONNECTED older than
`HALLA_STATUS_TTL_SECONDS` is re-proven live before it is shown. A stored row, a credential or a
saved configuration are never, on their own, "connected".
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx

from app.core.config import get_settings
from app.integrations.credential_store import CredentialDecryptionError, decrypt_credential
from app.integrations.workforce.contract import (
    LeadSyncResult,
    OutboundCallResult,
    WorkforceAgent,
    WorkforceAgentSpec,
    WorkforceIntegration,
    WorkforceNotConnectedError,
    WorkforceStatus,
    WorkforceStatusReport,
    WorkforceUnavailableError,
)
from app.integrations.workforce.halla_client import HallaClient, HallaConfigError, endpoint_from_settings
from app.models.integration import ConnectionStatus
from app.services.integration_connection_service import ConnectionNotFoundError, IntegrationConnectionService

PROVIDER = "halla"


def _first(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return None


def _unwrap(doc: dict[str, Any]) -> dict[str, Any]:
    inner = doc.get("data")
    return inner if isinstance(inner, dict) else doc


class HallaWorkforceIntegration(WorkforceIntegration):
    provider = PROVIDER

    def __init__(
        self,
        connections: IntegrationConnectionService,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep=None,
    ) -> None:
        self._connections = connections
        self._transport = transport
        self._sleep = sleep

    # ------------------------------------------------------------------ helpers

    def _report(self, status: WorkforceStatus, message: str) -> WorkforceStatusReport:
        return WorkforceStatusReport(
            status=status, adapter_implemented=True, provider=self.provider, message=message,
            checked_at=datetime.now(timezone.utc).isoformat(), mode="live",
        )

    async def _credential(self, tenant_id: UUID) -> tuple[dict[str, Any], Any]:
        conn = await self._connections.get_connection(tenant_id, PROVIDER)
        if conn is None or not conn.encrypted_credential or conn.status == ConnectionStatus.DISCONNECTED:
            raise WorkforceNotConnectedError("Halla is not connected for this business.")
        try:
            cred = decrypt_credential(conn.encrypted_credential)
        except CredentialDecryptionError as exc:
            raise WorkforceNotConnectedError("The stored Halla credential can no longer be read — reconnect Halla.") from exc
        if not cred.get("api_key"):
            raise WorkforceNotConnectedError("Halla is not connected for this business.")
        return cred, conn

    def _client(self, cred: dict[str, Any], *, retries: int | None = None, timeout: float | None = None) -> HallaClient:
        s = get_settings()
        try:
            endpoint = endpoint_from_settings(s)
        except HallaConfigError as exc:
            raise WorkforceUnavailableError(f"Halla is not configured on this Klaros deployment: {exc}") from exc
        kwargs: dict[str, Any] = {}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        if self._sleep is not None:
            kwargs["sleep"] = self._sleep
        return HallaClient(
            endpoint, cred["api_key"],
            timeout=timeout if timeout is not None else s.HALLA_REQUEST_TIMEOUT_SECONDS,
            max_retries=retries if retries is not None else s.HALLA_MAX_RETRIES,
            **kwargs,
        )

    # ------------------------------------------------------------------- status

    async def get_status(self, tenant_id: UUID) -> WorkforceStatusReport:
        s = get_settings()
        try:
            endpoint_from_settings(s)
        except HallaConfigError:
            return self._report(
                WorkforceStatus.CONFIGURATION_REQUIRED,
                "Halla isn't configured on this Klaros deployment yet — an operator must set its endpoint and credential header.",
            )
        conn = await self._connections.get_connection(tenant_id, PROVIDER)
        if conn is None or not conn.encrypted_credential or conn.status == ConnectionStatus.DISCONNECTED:
            return self._report(WorkforceStatus.NOT_CONNECTED, "Halla is not connected for this business yet.")
        if conn.status == ConnectionStatus.CONNECTING:
            return self._report(WorkforceStatus.CONNECTING, "Connecting to Halla…")
        if conn.status == ConnectionStatus.CONNECTED:
            fresh = conn.last_verified_at is not None and (datetime.now(timezone.utc) - _aware(conn.last_verified_at)).total_seconds() <= s.HALLA_STATUS_TTL_SECONDS
            return self._report(WorkforceStatus.CONNECTED, "Connected to Halla.") if fresh else await self.health_check(tenant_id)
        return self._error_report(conn.last_error)

    def _error_report(self, detail: str | None) -> WorkforceStatusReport:
        text = detail or "Halla could not be reached."
        attention = "different tenant" in text or "signing secret" in text
        return self._report(WorkforceStatus.NEEDS_ATTENTION if attention else WorkforceStatus.ERROR, text)

    async def health_check(self, tenant_id: UUID) -> WorkforceStatusReport:
        """A real request to Halla, every time. The only path that can make a connection CONNECTED."""
        try:
            endpoint_from_settings(get_settings())
        except HallaConfigError:
            return await self.get_status(tenant_id)
        try:
            conn = await self._connections.verify(tenant_id, PROVIDER)
        except ConnectionNotFoundError:
            return self._report(WorkforceStatus.NOT_CONNECTED, "Halla is not connected for this business yet.")
        if conn.status == ConnectionStatus.CONNECTED:
            return self._report(WorkforceStatus.CONNECTED, "Connected to Halla.")
        return self._error_report(conn.last_error)

    # --------------------------------------------------------------- operations

    async def get_workforce(self, tenant_id: UUID) -> dict[str, Any]:
        cred, _ = await self._credential(tenant_id)
        return _unwrap(await self._client(cred).get_workforce())

    async def configure(self, tenant_id: UUID, config: dict[str, Any]) -> WorkforceStatusReport:
        await self.configure_workforce(tenant_id, config)
        return await self.get_status(tenant_id)

    async def configure_workforce(self, tenant_id: UUID, config: dict[str, Any]) -> dict[str, Any]:
        cred, _ = await self._credential(tenant_id)
        return _unwrap(await self._client(cred).put_workforce(config))

    async def list_agents(self, tenant_id: UUID) -> list[WorkforceAgent]:
        cred, _ = await self._credential(tenant_id)
        doc = await self._client(cred).list_agents()
        raw = doc.get("agents") if isinstance(doc.get("agents"), list) else _unwrap(doc).get("agents") if isinstance(_unwrap(doc).get("agents"), list) else doc.get("data")
        agents: list[WorkforceAgent] = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            ident = _first(item, "id", "agent_id", "agentId")
            if ident is None:
                continue
            active = _first(item, "available", "active", "is_active")
            agents.append(
                WorkforceAgent(
                    id=str(ident)[:120],
                    name=str(_first(item, "name", "agent_name", "agentName") or "AI agent")[:120],
                    role=(str(item["role"])[:60] if item.get("role") else None),
                    status=(str(item["status"])[:40] if item.get("status") else ("active" if active else None)),
                    available=bool(active) if active is not None else None,
                )
            )
        return agents

    async def sync_lead(self, tenant_id: UUID, lead: dict[str, Any], *, external_id: str | None) -> LeadSyncResult:
        cred, _ = await self._credential(tenant_id)
        client = self._client(cred)
        body = {
            "klarosLeadId": lead["id"],  # Klaros' id is always carried; Halla's id never replaces it
            "name": lead.get("name"),
            "phoneNumber": lead.get("phone"),
            "email": lead.get("email"),
            "service": lead.get("service"),
            "source": lead.get("source") or "klaros",
            "notes": lead.get("notes"),
            "metadata": {"klaros_lead_id": lead["id"]},
        }
        body = {k: v for k, v in body.items() if v not in (None, "")}
        if external_id:
            doc = await client.update_lead(external_id, body)
            return LeadSyncResult(external_id=external_id, created=False)
        doc = await client.create_lead(body)
        inner = _unwrap(doc)
        lead_doc = inner.get("lead") if isinstance(inner.get("lead"), dict) else inner
        new_id = _first(lead_doc, "id", "lead_id", "leadId")
        if new_id is None:
            raise WorkforceUnavailableError("Halla did not return an id for the lead")
        return LeadSyncResult(external_id=str(new_id)[:120], created=True)

    async def initiate_outbound_call(
        self, tenant_id: UUID, *, klaros_lead_id: UUID, to_number: str, reason: str, opening_context: str | None
    ) -> OutboundCallResult:
        cred, _ = await self._credential(tenant_id)
        body: dict[str, Any] = {"toNumber": to_number, "reason": reason, "klarosLeadId": str(klaros_lead_id)}
        if opening_context:
            body["openingContext"] = opening_context
        doc = await self._client(cred).outbound_call(body)
        inner = _unwrap(doc)
        cid = _first(inner, "callSid", "call_sid", "callId", "call_id", "id")
        return OutboundCallResult(call_id=str(cid)[:120] if cid else None)

    # ---- contract methods that do not apply to a platform Klaros does not deploy agents on ----

    async def deploy_agent(self, tenant_id: UUID, spec: WorkforceAgentSpec) -> dict[str, Any]:
        raise WorkforceNotConnectedError("Klaros configures the Halla workforce; it does not deploy agents.")

    async def get_agent(self, tenant_id: UUID) -> dict[str, Any] | None:
        return None

    async def update_agent(self, tenant_id: UUID, spec: WorkforceAgentSpec) -> dict[str, Any]:
        raise WorkforceNotConnectedError("Klaros configures the Halla workforce; it does not deploy agents.")


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def halla_verifier(credential: dict) -> tuple[bool, str]:
    """The real verification `IntegrationConnectionService` runs for provider "halla": one health request
    with this tenant's own credential. Returns a short status string — never the response body."""
    api_key, mapped = credential.get("api_key"), credential.get("halla_tenant_id")
    if not api_key or not mapped:
        return False, "credential is missing the Halla API key or tenant id"
    if not credential.get("signing_secret"):
        return False, "the Halla signing secret is missing, so Halla's events cannot be verified"
    s = get_settings()
    try:
        client = HallaClient(endpoint_from_settings(s), api_key, timeout=s.HALLA_HEALTH_TIMEOUT_SECONDS, max_retries=0)
        doc = await client.health()
    except HallaConfigError:
        return False, "Halla is not configured on this Klaros deployment"
    except WorkforceUnavailableError as exc:
        return False, str(exc)
    inner = _unwrap(doc)
    # Halla's health answers {authenticated, tenantExists, status}. A 2xx alone is not enough: the key must actually have
    # authenticated, the tenant must exist, and Halla must call itself healthy. Only what is stated is checked.
    if inner.get("authenticated") is False:
        return False, "Halla did not authenticate the API key"
    if inner.get("tenantExists") is False:
        return False, "Halla does not know that tenant"
    reported = _first(inner, "tenant_id", "tenantId")
    if reported is not None and str(reported) != str(mapped):
        return False, "Halla reports a different tenant than the one connected"
    state = inner.get("status")
    if isinstance(state, str) and state.strip().lower() not in ("healthy", "ok", "up", "ready"):
        return False, f"Halla reports its status as '{state.strip()[:30]}'"
    return True, "verified via Halla health"
