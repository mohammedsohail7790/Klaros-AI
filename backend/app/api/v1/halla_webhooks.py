"""Halla -> Klaros webhooks: the one public endpoint Halla delivers signed events to.

    POST /api/v1/webhooks/halla/{tenant_id}

There is no JWT here: the signature is the only trust boundary, so NOTHING is read from the payload or acted on
before it verifies. The pipeline is:

  1. read the RAW bytes (never parse-and-re-serialise before verifying)
  2. find THIS Klaros tenant's Halla connection (the path only selects whose secret to verify against;
     it grants nothing — a forged request cannot pass without that tenant's signing secret)
  3. verify X-HallaAI-Timestamp / X-HallaAI-Signature = HMAC-SHA256(timestamp + "." + raw_body),
     constant-time, with a freshness window
  4. parse the envelope; reject unknown event types
  5. the envelope's tenant_id must equal the Halla tenant id stored for this Klaros tenant — the payload never
     chooses a Klaros tenant
  6. de-duplicate on the Halla event id (the existing WebhookEvent unique constraint)
  7. apply it to the existing operating layer (leads, event bus -> automations, appointments)
"""

import uuid
from datetime import datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.api.tool_deps import get_wired_event_bus
from app.api.tool_deps_integrations import get_integration_connection_service
from app.core.config import get_settings
from app.core.rate_limit import rate_limit, tenant_and_ip_key
from app.db.session import async_session_maker, set_tenant_context
from app.events.bus import EventBus
from app.integrations.credential_store import CredentialDecryptionError, decrypt_credential
from app.integrations.workforce import halla_webhook as hw
from app.models.integration import ConnectionStatus, WebhookEvent, WebhookProcessingStatus
from app.services.halla_event_processor import HallaEventProcessor

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/webhooks/halla", tags=["webhooks"])

PROVIDER = "halla"

_rate_limit = Depends(
    rate_limit("halla_webhook", limit_setting="RATE_LIMIT_WEBHOOK_PER_MINUTE", window_seconds=60, key_func=tenant_and_ip_key)
)


@router.post("/{tenant_id}", status_code=status.HTTP_200_OK, response_model=None, dependencies=[_rate_limit])
async def halla_webhook(
    tenant_id: uuid.UUID,
    request: Request,
    timestamp: str | None = Header(default=None, alias="X-HallaAI-Timestamp"),
    signature: str | None = Header(default=None, alias="X-HallaAI-Signature"),
    bus: EventBus = Depends(get_wired_event_bus),
) -> dict[str, str] | JSONResponse:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > hw.MAX_BODY_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="payload too large")  # before reading it
    raw_body = await request.body()
    if len(raw_body) > hw.MAX_BODY_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="payload too large")

    connections = get_integration_connection_service()
    conn = await connections.get_connection(tenant_id, PROVIDER)
    secret, mapped = None, None
    if conn is not None and conn.encrypted_credential and conn.status != ConnectionStatus.DISCONNECTED:
        try:
            cred = decrypt_credential(conn.encrypted_credential)
            secret, mapped = cred.get("signing_secret"), conn.external_account_id
        except CredentialDecryptionError:
            secret = None
    if not secret or not mapped:
        # Unknown tenant, no connection, or no secret: indistinguishable on purpose.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

    try:
        hw.verify_signature(raw_body, timestamp, signature, secret, tolerance_seconds=get_settings().HALLA_WEBHOOK_TOLERANCE_SECONDS)
    except hw.HallaWebhookError as exc:
        logger.warning("halla_webhook_rejected", tenant_id=str(tenant_id), reason=exc.reason)
        raise HTTPException(status_code=exc.status, detail="invalid signature") from exc

    try:
        env = hw.parse_envelope(raw_body)
    except hw.HallaWebhookError as exc:
        logger.warning("halla_webhook_envelope_rejected", tenant_id=str(tenant_id), reason=exc.reason)
        raise HTTPException(status_code=exc.status, detail=exc.reason.replace("_", " ")) from exc

    if env.tenant_id != str(mapped):
        # Authentic delivery, but for a different Halla tenant than the one connected to this Klaros tenant.
        logger.warning("halla_webhook_tenant_mismatch", tenant_id=str(tenant_id), event_id=env.id, event_type=env.type)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="tenant mismatch")

    external_event_id = f"{tenant_id}:{env.id}"
    async with async_session_maker() as session:
        await set_tenant_context(session, tenant_id)  # the tenant is resolved and verified by now; webhook_events is RLS-enforced
        existing = (
            await session.execute(select(WebhookEvent).where(WebhookEvent.provider == PROVIDER, WebhookEvent.external_event_id == external_event_id))
        ).scalar_one_or_none()
        if existing is not None:
            # Atomic claim (compare-and-set on the row itself, so it holds across workers and processes): a FAILED event is retryable at once; a
            # RECEIVED event only once its lease has genuinely expired (the worker that owned it crashed). PROCESSED / anything else is final.
            # The lease is the row's own `updated_at`, stamped by the claim, so no schema change is needed.
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=get_settings().HALLA_WEBHOOK_LEASE_SECONDS)
            claim = await session.execute(
                update(WebhookEvent)
                .where(
                    WebhookEvent.id == existing.id,
                    or_(
                        WebhookEvent.status == WebhookProcessingStatus.FAILED,
                        and_(WebhookEvent.status == WebhookProcessingStatus.RECEIVED, WebhookEvent.updated_at < cutoff),
                    ),
                )
                .values(status=WebhookProcessingStatus.RECEIVED, error_detail=None, updated_at=func.now())
                .execution_options(synchronize_session=False)  # a database-side compare-and-set; never evaluated in Python
            )
            await session.commit()
            if not claim.rowcount:
                logger.info("halla_webhook_duplicate_ignored", event_id=env.id, event_type=env.type, was=str(existing.status))
                return {"status": "duplicate_ignored"}
            logger.warning("halla_webhook_event_reclaimed", event_id=env.id, event_type=env.type, was=str(existing.status))
            row_id = existing.id
        else:
            row = WebhookEvent(
                provider=PROVIDER, external_event_id=external_event_id, event_type=env.type, tenant_id=tenant_id,
                status=WebhookProcessingStatus.RECEIVED,
                # Only the envelope's own identifying fields — not the data object (summaries and PII stay in Halla/Klaros' lead).
                raw_payload={"id": env.id, "type": env.type, "timestamp": env.timestamp, "tenant_id": env.tenant_id},
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return {"status": "duplicate_ignored"}  # a concurrent delivery of the same event won
            row_id = row.id
    try:
        result = await HallaEventProcessor(async_session_maker).process(tenant_id, env, bus)
    except Exception as exc:  # noqa: BLE001 - recorded FAILED so Halla's redelivery is reprocessed, never lost
        logger.error("halla_webhook_processing_failed", tenant_id=str(tenant_id), event_id=env.id, event_type=env.type, error_type=type(exc).__name__)
        async with async_session_maker() as session:
            await set_tenant_context(session, tenant_id)
            # Only a worker that still owns the event may mark it failed: if the lease expired and another worker already finished it, that
            # result stands (never regress PROCESSED to FAILED).
            await session.execute(
                update(WebhookEvent)
                .where(WebhookEvent.id == row_id, WebhookEvent.status == WebhookProcessingStatus.RECEIVED)
                .values(status=WebhookProcessingStatus.FAILED, error_detail=type(exc).__name__, updated_at=func.now())
                .execution_options(synchronize_session=False)
            )
            await session.commit()
        return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"status": "processing_failed"})

    async with async_session_maker() as session:
        await set_tenant_context(session, tenant_id)  # the tenant is resolved and verified by now; webhook_events is RLS-enforced
        row = await session.get(WebhookEvent, row_id)
        if row is not None:
            row.status = WebhookProcessingStatus.PROCESSED
            row.error_detail = None if result.get("handled") else result.get("note")
            await session.commit()
    logger.info("halla_webhook_processed", tenant_id=str(tenant_id), event_id=env.id, event_type=env.type, handled=result.get("handled"))
    return {"status": "ok"}
