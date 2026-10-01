"""Klaros's own SaaS subscription billing — real Stripe Checkout
(subscription mode) + Billing Portal + webhook sync, entirely separate
from app/api/v1/webhooks.py's tenant-owned Stripe Connect webhook. See
app/services/billing_service.py for the business logic this router is a
thin HTTP wrapper over.
"""

import uuid
from datetime import UTC, datetime
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db
from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.integrations.stripe_client import StripeWebhookPayloadError, StripeWebhookSignatureError, verify_webhook_signature
from app.integrations.stripe_schemas import StripeCheckoutSessionPayload, StripeSubscriptionPayload, StripeWebhookEnvelope
from app.models.integration import WebhookEvent, WebhookProcessingStatus
from app.models.organization import Organization
from app.services.billing_service import (
    BillingNotConfiguredError,
    BillingService,
    NoBillingCustomerError,
    UnknownPlanError,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


def get_billing_service() -> BillingService:
    return BillingService(async_session_maker)


async def _get_org(db: AsyncSession, current_user: CurrentUser) -> Organization:
    org = await db.get(Organization, current_user.tenant_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return org


@router.get("/status")
async def get_status(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    billing_service: BillingService = Depends(get_billing_service),
) -> dict:
    org = await _get_org(db, current_user)
    result = await billing_service.get_billing_status(org)
    return {
        "plan": result.plan,
        "billing_status": result.billing_status,
        "trial_ends_at": result.trial_ends_at.isoformat() if result.trial_ends_at else None,
        "current_period_end": result.current_period_end.isoformat() if result.current_period_end else None,
        "ai_usage_this_month": result.ai_usage_this_month,
        "ai_usage_limit": result.ai_usage_limit,
    }


class CheckoutRequest(BaseModel):
    plan: Literal["solo", "growth"]
    success_url: str
    cancel_url: str


@router.post("/checkout")
async def create_checkout(
    body: CheckoutRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    billing_service: BillingService = Depends(get_billing_service),
) -> dict:
    org = await _get_org(db, current_user)
    try:
        checkout_url = await billing_service.create_checkout_session(
            org, body.plan, success_url=body.success_url, cancel_url=body.cancel_url
        )
    except BillingNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except UnknownPlanError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"checkout_url": checkout_url}


class PortalRequest(BaseModel):
    return_url: str


@router.post("/portal")
async def create_portal(
    body: PortalRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    billing_service: BillingService = Depends(get_billing_service),
) -> dict:
    org = await _get_org(db, current_user)
    try:
        portal_url = await billing_service.create_portal_session(org, return_url=body.return_url)
    except BillingNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except NoBillingCustomerError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"portal_url": portal_url}


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def billing_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
    billing_service: BillingService = Depends(get_billing_service),
) -> dict[str, str]:
    settings = get_settings()
    if not settings.STRIPE_PLATFORM_WEBHOOK_SECRET:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Billing webhooks not configured")

    raw_body = await request.body()
    try:
        event = verify_webhook_signature(raw_body, stripe_signature or "", settings.STRIPE_PLATFORM_WEBHOOK_SECRET)
    except StripeWebhookSignatureError as exc:
        logger.warning("billing_webhook_signature_rejected", error=str(exc))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid signature") from exc
    except StripeWebhookPayloadError as exc:
        logger.warning("billing_webhook_payload_malformed", error=str(exc))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="malformed webhook payload") from exc

    try:
        envelope = StripeWebhookEnvelope.model_validate(event)
    except ValidationError as exc:
        logger.warning("billing_webhook_envelope_invalid")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="malformed webhook envelope") from exc

    external_event_id = envelope.id
    event_type = envelope.type

    # Phase 17B-2R classification: tenant identity is not yet knowable
    # here — it is only resolved below, from the verified webhook
    # payload's own metadata, after signature verification (same
    # "resolve-then-stamp" reasoning as app/api/v1/webhooks.py). This
    # session only creates/updates the WebhookEvent row itself (nullable
    # tenant_id until resolved), so genuinely no tenant context to set yet.
    async with async_session_maker() as session:
        existing = (
            await session.execute(
                select(WebhookEvent).where(
                    WebhookEvent.provider == "stripe_platform", WebhookEvent.external_event_id == external_event_id
                )
            )
        ).scalar_one_or_none()
        if existing is not None and existing.status != WebhookProcessingStatus.FAILED:
            logger.info("billing_webhook_duplicate_ignored", event_id=external_event_id)
            return {"status": "duplicate_ignored"}

        if existing is not None:
            webhook_row = existing
            webhook_row.raw_payload = event
            webhook_row.status = WebhookProcessingStatus.RECEIVED
            await session.commit()
        else:
            webhook_row = WebhookEvent(
                provider="stripe_platform",
                external_event_id=external_event_id,
                event_type=event_type,
                raw_payload=event,
                status=WebhookProcessingStatus.RECEIVED,
            )
            session.add(webhook_row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return {"status": "duplicate_ignored"}

    tenant_id: uuid.UUID | None = None
    status_value = WebhookProcessingStatus.PROCESSED
    error_detail: str | None = None

    try:
        if event_type == "checkout.session.completed":
            payload = StripeCheckoutSessionPayload.model_validate(envelope.data.object)
            if payload.mode == "subscription" and payload.metadata.tenant_id and payload.customer and payload.subscription:
                tenant_id = uuid.UUID(payload.metadata.tenant_id)
                plan = payload.metadata.model_extra.get("plan") if payload.metadata.model_extra else None
                if plan:
                    await billing_service.apply_checkout_completed(tenant_id, plan, payload.customer, payload.subscription)
                else:
                    error_detail = "checkout.session.completed missing metadata.plan"
        elif event_type == "customer.subscription.updated":
            payload = StripeSubscriptionPayload.model_validate(envelope.data.object)
            period_end = (
                datetime.fromtimestamp(payload.current_period_end, tz=UTC) if payload.current_period_end else None
            )
            await billing_service.apply_subscription_updated(
                payload.id, status=payload.status or "active", current_period_end=period_end
            )
        elif event_type == "customer.subscription.deleted":
            payload = StripeSubscriptionPayload.model_validate(envelope.data.object)
            await billing_service.apply_subscription_deleted(payload.id)
        else:
            logger.info("billing_webhook_ignored_event_type", event_type=event_type)
    except Exception as exc:  # noqa: BLE001 — must still be recorded, not crash the endpoint
        status_value = WebhookProcessingStatus.FAILED
        error_detail = str(exc)
        logger.error("billing_webhook_processing_failed", event_id=external_event_id, error=str(exc))

    async with async_session_maker() as session:
        # tenant_id is resolved above from the verified payload (may still
        # be None for event types that carry no tenant metadata, e.g.
        # customer.subscription.updated/.deleted) — set_tenant_context is
        # a safe no-op on None, never a fabricated value.
        await set_tenant_context(session, tenant_id)
        row = await session.get(WebhookEvent, webhook_row.id)
        if row is not None:
            row.status = status_value
            row.tenant_id = tenant_id
            row.error_detail = error_detail[:500] if error_detail else None
            await session.commit()

    return {"status": "processed" if status_value == WebhookProcessingStatus.PROCESSED else "failed"}
