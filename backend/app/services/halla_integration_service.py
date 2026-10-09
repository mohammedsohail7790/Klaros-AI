"""Klaros -> Halla operations, tenant by tenant: connect, health, configure, agents, lead sync, outbound call.

Klaros orchestrates; Halla executes. Everything here goes through the `WorkforceIntegration` contract
(the Halla adapter), so nothing in this module knows Halla's HTTP details. The authenticated Klaros tenant
is the only tenant ever used — the browser supplies no tenant id, no URL and no credential after the
initial connect call (and that credential is encrypted at once and never returned).
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.db.session import set_tenant_context
from app.integrations.workforce import (
    WorkforceIntegration,
    WorkforceNotConnectedError,
    WorkforceStatusReport,
    WorkforceUnavailableError,
    get_workforce_integration,
    halla_enabled,
)
from app.models.crm import Lead
from app.services.integration_connection_service import ConnectionNotFoundError, IntegrationConnectionService

logger = structlog.get_logger(__name__)

PROVIDER = "halla"
_TENANT_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")


class HallaNotEnabledError(Exception):
    """This Klaros deployment does not run the real Halla adapter (WORKFORCE_ADAPTER != halla)."""


class InvalidHallaCredentialError(ValueError):
    pass


class LeadNotFoundError(Exception):
    pass


class LeadNotCallableError(Exception):
    pass


class LeadNotSyncableError(Exception):
    """The lead cannot be created in Halla as it stands (Halla requires a phone number of 3-32 characters)."""


# Halla's contract for POST /api/v1/leads: `phoneNumber` is required, 3-32 characters.
HALLA_PHONE_MIN, HALLA_PHONE_MAX = 3, 32


def build_halla_workforce_config(pack: dict[str, Any]) -> dict[str, Any]:
    """Klaros' business context -> the body of PUT /integrations/klaros/workforce.

    Business configuration only (what the business offers, where, what to ask, when to hand over, how to book).
    Generic: nothing here names a business type."""
    fields = pack.get("qualification_fields") or []
    return {
        "businessName": pack.get("business_name"),
        "industry": pack.get("industry"),
        "businessDescription": pack.get("summary"),
        "customers": pack.get("customers"),
        "servicesOffered": pack.get("services") or [],
        "serviceAreas": pack.get("markets") or [],
        "qualificationQuestions": [f"Ask for: {f}" for f in fields],
        "requiredFields": fields,
        "escalationTriggers": pack.get("escalation_triggers") or [],
        "autoTransferEnabled": bool(pack.get("escalation_triggers")),
        "bookingRules": pack.get("booking_rules") or [],
        "source": "klaros",
    }


class HallaIntegrationService:
    def __init__(self, session_factory: async_sessionmaker, connections: IntegrationConnectionService) -> None:
        self._session_factory = session_factory
        self._connections = connections

    def _adapter(self) -> WorkforceIntegration:
        if not halla_enabled():
            raise HallaNotEnabledError("The Halla integration is not enabled on this Klaros deployment.")
        return get_workforce_integration()

    @staticmethod
    def webhook_url(tenant_id: uuid.UUID) -> str | None:
        base = get_settings().KLAROS_PUBLIC_API_URL
        return f"{base.rstrip('/')}/api/v1/webhooks/halla/{tenant_id}" if base else None

    # --------------------------------------------------------------- connection

    async def connect(self, tenant_id: uuid.UUID, user_id: uuid.UUID | None, *, halla_tenant_id: str, api_key: str, signing_secret: str, verify: bool = True) -> WorkforceStatusReport:
        adapter = self._adapter()
        halla_tenant_id, api_key, signing_secret = (halla_tenant_id or "").strip(), (api_key or "").strip(), (signing_secret or "").strip()
        if not _TENANT_ID_RE.match(halla_tenant_id):
            raise InvalidHallaCredentialError("The Halla tenant id is not valid.")
        for label, v in (("API key", api_key), ("signing secret", signing_secret)):
            if not (8 <= len(v) <= 512) or re.search(r"\s|[\x00-\x1f]", v):
                raise InvalidHallaCredentialError(f"The Halla {label} is not valid.")
        # Encrypted at once with the existing credential store. A real health request then decides the status.
        await self._connections.connect(
            tenant_id, PROVIDER,
            {"api_key": api_key, "signing_secret": signing_secret, "halla_tenant_id": halla_tenant_id},
            created_by=user_id, external_account_id=halla_tenant_id, verify=verify,
        )
        logger.info("halla_connection_saved", tenant_id=str(tenant_id), operation="connect")
        return await adapter.get_status(tenant_id)

    async def disconnect(self, tenant_id: uuid.UUID) -> WorkforceStatusReport:
        adapter = self._adapter()
        try:
            await self._connections.disconnect(tenant_id, PROVIDER)
        except ConnectionNotFoundError:
            pass
        return await adapter.get_status(tenant_id)

    async def health(self, tenant_id: uuid.UUID) -> WorkforceStatusReport:
        return await self._adapter().health_check(tenant_id)

    async def connection_info(self, tenant_id: uuid.UUID) -> dict[str, Any]:
        """Non-secret facts about the stored connection — never the credential itself."""
        conn = await self._connections.get_connection(tenant_id, PROVIDER)
        has_secret = False
        if conn is not None and conn.encrypted_credential:
            from app.integrations.credential_store import CredentialDecryptionError, decrypt_credential

            try:
                has_secret = bool(decrypt_credential(conn.encrypted_credential).get("signing_secret"))
            except CredentialDecryptionError:
                has_secret = False
        return {
            "halla_tenant_id": conn.external_account_id if conn is not None else None,
            "has_credential": bool(conn is not None and conn.encrypted_credential),
            "has_signing_secret": has_secret,
            "last_verified_at": conn.last_verified_at.isoformat() if conn is not None and conn.last_verified_at else None,
            "last_error": conn.last_error if conn is not None else None,
            "webhook_url": self.webhook_url(tenant_id),
        }

    # ------------------------------------------------------------- orchestration

    async def configure(self, tenant_id: uuid.UUID, context_pack: dict[str, Any]) -> dict[str, Any]:
        adapter = self._adapter()
        body = build_halla_workforce_config(context_pack)
        await adapter.configure_workforce(tenant_id, body)
        return {"sent": {k: (len(v) if isinstance(v, list) else bool(v)) for k, v in body.items() if k != "source"}}

    async def workforce(self, tenant_id: uuid.UUID) -> dict[str, Any]:
        return await self._adapter().get_workforce(tenant_id)

    async def agents(self, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
        return [
            {"id": a.id, "name": a.name, "role": a.role, "status": a.status, "available": a.available}
            for a in await self._adapter().list_agents(tenant_id)
        ]

    async def _lead(self, session, tenant_id: uuid.UUID, lead_id: uuid.UUID) -> Lead:
        lead = (await session.execute(select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id))).scalar_one_or_none()
        if lead is None:
            raise LeadNotFoundError("lead not found")
        return lead

    async def sync_lead(self, tenant_id: uuid.UUID, lead_id: uuid.UUID) -> dict[str, Any]:
        adapter = self._adapter()
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            lead = await self._lead(session, tenant_id, lead_id)
            payload = {
                "id": str(lead.id), "name": lead.name, "phone": lead.phone, "email": lead.email,
                "service": lead.service_requested, "source": lead.source, "notes": (lead.description or "")[:1000] or None,
            }
            existing = lead.external_id if lead.external_provider == PROVIDER else None
        if existing is None:
            # Creating the lead in Halla needs a valid phone. Checked here so Halla is never called with a payload it
            # will refuse, and so the caller gets a clear 4xx instead of Halla's 400 surfacing as a generic 502.
            # The number is never truncated or altered (only surrounding whitespace is ignored).
            phone = (payload.get("phone") or "").strip()
            if not phone:
                raise LeadNotSyncableError("This lead has no phone number, which Halla requires. Add one before sending it to Halla.")
            if not (HALLA_PHONE_MIN <= len(phone) <= HALLA_PHONE_MAX):
                raise LeadNotSyncableError(
                    f"Halla requires a phone number of {HALLA_PHONE_MIN}-{HALLA_PHONE_MAX} characters; this one has {len(phone)}. "
                    "Correct it before sending it to Halla."
                )
            payload["phone"] = phone
        result = await adapter.sync_lead(tenant_id, payload, external_id=existing)
        if existing is None:
            async with self._session_factory() as session:
                await set_tenant_context(session, tenant_id)
                lead = await self._lead(session, tenant_id, lead_id)
                lead.external_provider, lead.external_id = PROVIDER, result.external_id
                try:
                    await session.commit()
                except IntegrityError as exc:
                    await session.rollback()
                    raise WorkforceUnavailableError("Halla returned a lead id that is already linked to another Klaros lead") from exc
        return {"synced": True, "created": result.created}

    async def call_lead(self, tenant_id: uuid.UUID, lead_id: uuid.UUID, *, reason: str, opening_context: str | None) -> dict[str, Any]:
        adapter = self._adapter()
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            lead = await self._lead(session, tenant_id, lead_id)
            phone = (lead.phone or "").strip()
            if phone:
                from app.services import halla_consent, pilot_safety

                profile = await pilot_safety.profile_in_session(session, tenant_id)
                if profile is not None and profile.requires_consent_evidence:
                    state = await halla_consent.state_for(session, tenant_id, halla_lead_id=lead.external_id, lead_id=lead.id)
                    if not state.allows(halla_consent.CONTACT):
                        raise LeadNotCallableError("Contact consent has not been established for this lead.")
        if not phone:
            raise LeadNotCallableError("This lead has no phone number to call.")
        result = await adapter.initiate_outbound_call(
            tenant_id, klaros_lead_id=lead_id, to_number=phone, reason=(reason or "follow_up")[:60],
            opening_context=(opening_context or None) and opening_context[:1000],
        )
        return {"requested": True, "call_id": result.call_id}
