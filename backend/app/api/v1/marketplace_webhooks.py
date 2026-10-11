"""Marketplace lead-ingestion webhooks (Angi, Thumbtack, Nextdoor) — see
app/integrations/marketplace_adapters.py for why this endpoint verifies a
Klaros-defined HMAC scheme rather than any marketplace's own (none of the
three publishes one).

Trust boundary: a per-tenant, per-provider shared secret stored via the
existing `IntegrationConnection` mechanism (`POST
/integrations/connections/{provider}/connect` with
`{"credential": {"webhook_secret": "...", "field_map": "<json-encoded
dict>"}}` — `field_map` is JSON-encoded because the shared `ConnectRequest`
schema types every credential value as `str`, provider one of
"angi"/"thumbtack"/"nextdoor" — the same generic endpoint already used for
Stripe/QuickBooks/Google Calendar). The tenant configures their
marketplace's own webhook/Zapier bridge to POST here with
`X-Klaros-Signature: hex(HMAC-SHA256(raw_body, webhook_secret))`.

Pipeline: verify tenant has a configured secret -> verify signature ->
deduplicate (WebhookEvent) -> normalize (MarketplaceAdapter) -> canonical
LeadService.create_lead -> audit trail via the same WebhookEvent row every
other provider webhook uses.
"""

import hashlib
import hmac
import json
import uuid

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.tool_deps import get_wired_event_bus
from app.api.tool_deps_integrations import get_integration_connection_service
from app.core.rate_limit import rate_limit, tenant_and_ip_key
from app.db.session import async_session_maker, set_tenant_context
from app.events.bus import EventBus
from app.integrations.credential_store import decrypt_credential
from app.integrations.marketplace_adapters import MARKETPLACE_ADAPTERS
from app.models.integration import WebhookEvent, WebhookProcessingStatus
from app.services.integration_connection_service import IntegrationConnectionService
from app.services.lead_service import CreateLeadInput, LeadService

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/webhooks/marketplace", tags=["marketplace-webhooks"])

_rate_limit_dependency = Depends(
    rate_limit(
        "marketplace_webhook",
        limit_setting="RATE_LIMIT_WEBHOOK_PER_MINUTE",
        window_seconds=60,
        key_func=tenant_and_ip_key,
    )
)


def _verify_signature(raw_body: bytes, signature: str | None, secret: str) -> bool:
    if not signature:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/{provider}/{tenant_id}", status_code=status.HTTP_200_OK, dependencies=[_rate_limit_dependency])
async def marketplace_lead_webhook(
    provider: str,
    tenant_id: uuid.UUID,
    request: Request,
    x_klaros_signature: str | None = Header(default=None, alias="X-Klaros-Signature"),
    bus: EventBus = Depends(get_wired_event_bus),
    connection_service: IntegrationConnectionService = Depends(get_integration_connection_service),
) -> dict:
    adapter = MARKETPLACE_ADAPTERS.get(provider)
    if adapter is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown marketplace provider {provider!r}")

    connection = await connection_service.get_connection(tenant_id, provider)
    if connection is None or not connection.encrypted_credential:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{provider} is not configured for this tenant (NOT_CONNECTED)",
        )
    credential = decrypt_credential(connection.encrypted_credential)
    secret = credential.get("webhook_secret")
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{provider} connection is missing a webhook_secret",
        )

    raw_body = await request.body()
    signature = x_klaros_signature or request.headers.get("x-klaros-signature")
    if not _verify_signature(raw_body, signature, secret):
        logger.warning("marketplace_webhook_signature_rejected", provider=provider, tenant_id=str(tenant_id))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid signature")

    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001 — malformed JSON is caller error, not a crash
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="malformed JSON body") from exc

    from app.services.consent_gate import tenant_requires_consent

    if await tenant_requires_consent(async_session_maker, tenant_id):
        # A marketplace lead carries no consent evidence: acknowledge (so the sender stops retrying) but store nothing, not even the raw payload.
        logger.warning("marketplace_lead_refused_consent_gated_tenant", provider=provider)
        return {"received": True, "deduplicated": False, "lead_id": None, "refused": "consent_required"}

    raw_field_map = credential.get("field_map")
    field_map = json.loads(raw_field_map) if raw_field_map else None
    try:
        normalized = adapter.normalize_lead(payload, field_map=field_map)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    external_event_id = f"{tenant_id}:{normalized.external_lead_id}"
    async with async_session_maker() as session:
        # tenant_id (path param) is only trusted from here on — it has
        # already been verified above via the tenant's own webhook_secret
        # signature (_verify_signature), so it is safe to stamp now.
        await set_tenant_context(session, tenant_id)
        existing = (
            await session.execute(
                select(WebhookEvent).where(
                    WebhookEvent.provider == f"marketplace_{provider}",
                    WebhookEvent.external_event_id == external_event_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return {"received": True, "deduplicated": True}

        webhook_row = WebhookEvent(
            tenant_id=tenant_id,
            provider=f"marketplace_{provider}",
            external_event_id=external_event_id,
            event_type="marketplace_lead",
            raw_payload=payload,
            status=WebhookProcessingStatus.RECEIVED,
        )
        session.add(webhook_row)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return {"received": True, "deduplicated": True}

        webhook_row.status = WebhookProcessingStatus.PROCESSED
        await session.commit()

    lead_service = LeadService(async_session_maker, bus)
    lead, _deduplicated = await lead_service.create_lead(
        tenant_id,
        CreateLeadInput(
            name=normalized.name,
            source="MARKETPLACE",
            phone=normalized.phone,
            email=normalized.email,
            source_detail=provider,
            service_requested=normalized.service_requested,
            description=normalized.description,
            location=normalized.location,
            idempotency_key=f"marketplace-{provider}-{normalized.external_lead_id}",
        ),
    )
    return {"received": True, "deduplicated": False, "lead_id": str(lead.id)}
