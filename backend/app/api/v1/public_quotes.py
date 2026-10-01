"""Phase 14: the public, UNAUTHENTICATED quote view/accept/decline
endpoints — Klaros' first customer-facing surface with no login. Trust
boundary is the signed `quote_view` token (`app/core/security.py::
create_quote_view_token`/`decode_quote_view_token`), the same pattern
already established for provider webhooks (`app/api/v1/webhooks.py`) and
the QuickBooks OAuth `state` token (`app/api/v1/quickbooks_oauth.py`):
`tenant_id`/`quote_id` are read ONLY from the verified token payload,
never from a client-supplied path/query value trusted on its own — the
`{quote_id}` path parameter is checked for equality against the token's
own `quote_id`, not used as authority.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.tool_deps_integrations import get_integration_connection_service
from app.core.config import get_settings
from app.core.rate_limit import path_param_and_ip_key, rate_limit
from app.api.tool_deps import get_wired_event_bus
from app.core.security import TokenError, decode_quote_view_token
from app.db.session import async_session_maker, set_tenant_context
from app.events.bus import EventBus
from app.models.quote import Quote, QuoteLineItem
from app.services.quote_deposit_service import (
    DepositCheckoutError,
    DepositNotRequiredError,
    InvalidDepositStateError,
    QuoteDepositService,
)
from app.services.quote_deposit_service import QuoteNotFoundError as DepositQuoteNotFoundError
from app.services.quote_deposit_service import StripeNotConfiguredError
from app.services.quote_service import (
    InvalidQuoteTransitionError,
    QuoteExpiredError,
    QuoteNotFoundError,
    QuoteService,
)

router = APIRouter(prefix="/public/quotes", tags=["public-quotes"])

_rate_limit_dependency = Depends(
    rate_limit(
        "public_quote",
        limit_setting="RATE_LIMIT_PUBLIC_QUOTE_PER_MINUTE",
        window_seconds=60,
        key_func=path_param_and_ip_key("quote_id"),
    )
)


def _quote_to_dict(q: Quote) -> dict[str, Any]:
    return {
        "id": str(q.id),
        "quote_number": q.quote_number,
        "status": q.status,
        "currency": q.currency,
        "subtotal": str(q.subtotal),
        "tax": str(q.tax),
        "discount": str(q.discount),
        "total": str(q.total),
        "notes": q.notes,
        "terms": q.terms,
        "valid_until": q.valid_until.isoformat() if q.valid_until else None,
        "decided_at": q.decided_at.isoformat() if q.decided_at else None,
        "deposit_required": q.deposit_type is not None,
        "deposit_amount": str(q.deposit_amount) if q.deposit_amount is not None else None,
        # Deliberately NEVER includes customer_id/lead_id/job_id/tenant_id,
        # deposit_type/deposit_value (internal configuration, not customer-
        # facing), or any Stripe/payment identifier — this response is
        # served to an unauthenticated browser.
    }


def _resolve_token(quote_id: uuid.UUID, token: str) -> uuid.UUID:
    try:
        payload = decode_quote_view_token(token)
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid or expired link") from exc
    if payload.get("quote_id") != str(quote_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid or expired link")
    return uuid.UUID(payload["tenant_id"])


def _service(bus: EventBus) -> QuoteService:
    return QuoteService(async_session_maker, bus)


async def _quote_to_dict_with_items(tenant_id: uuid.UUID, quote: Quote) -> dict[str, Any]:
    """`_quote_to_dict` alone omits line_items — every public response
    (view/accept/decline) declares them as part of its shape (see the
    frontend's `PublicQuote` type), so every handler below must attach
    them the same way `view_quote` always has, not just the GET path."""
    async with async_session_maker() as session:
        # tenant_id here is only ever the verified, signed quote_view
        # token's own payload (see _resolve_token above) — never a
        # client-supplied path/query value trusted on its own.
        await set_tenant_context(session, tenant_id)
        items = (
            await session.execute(
                select(QuoteLineItem)
                .where(QuoteLineItem.tenant_id == tenant_id, QuoteLineItem.quote_id == quote.id)
                .order_by(QuoteLineItem.sort_order)
            )
        ).scalars().all()

    result = _quote_to_dict(quote)
    result["line_items"] = [
        {
            "description": i.description, "quantity": str(i.quantity), "unit_price": str(i.unit_price),
            "discount": str(i.discount), "tax_rate": str(i.tax_rate), "line_total": str(i.line_total),
        }
        for i in items
    ]
    return result


@router.get("/{quote_id}", dependencies=[_rate_limit_dependency])
async def view_quote(
    quote_id: uuid.UUID, token: str = Query(...), bus: EventBus = Depends(get_wired_event_bus)
) -> dict[str, Any]:
    tenant_id = _resolve_token(quote_id, token)
    try:
        quote = await _service(bus).get_for_public_view(tenant_id, quote_id)
    except QuoteNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quote not found") from exc
    return await _quote_to_dict_with_items(tenant_id, quote)


class DeclineInput(BaseModel):
    reason: str | None = None


@router.post("/{quote_id}/accept", dependencies=[_rate_limit_dependency])
async def accept_quote(
    quote_id: uuid.UUID, token: str = Query(...), bus: EventBus = Depends(get_wired_event_bus)
) -> dict[str, Any]:
    tenant_id = _resolve_token(quote_id, token)
    try:
        result = await _service(bus).decide(tenant_id, quote_id, accepted=True)
    except QuoteNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quote not found") from exc
    except QuoteExpiredError as exc:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc)) from exc
    except InvalidQuoteTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"quote": await _quote_to_dict_with_items(tenant_id, result.quote), "job_created": result.job is not None}


@router.post("/{quote_id}/decline", dependencies=[_rate_limit_dependency])
async def decline_quote(
    quote_id: uuid.UUID, body: DeclineInput, token: str = Query(...), bus: EventBus = Depends(get_wired_event_bus)
) -> dict[str, Any]:
    tenant_id = _resolve_token(quote_id, token)
    try:
        result = await _service(bus).decide(tenant_id, quote_id, accepted=False, decline_reason=body.reason)
    except QuoteNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quote not found") from exc
    except QuoteExpiredError as exc:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc)) from exc
    except InvalidQuoteTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"quote": await _quote_to_dict_with_items(tenant_id, result.quote)}


def _deposit_service() -> QuoteDepositService:
    return QuoteDepositService(async_session_maker, get_integration_connection_service())


@router.post("/{quote_id}/deposit/checkout", dependencies=[_rate_limit_dependency])
async def create_deposit_checkout(quote_id: uuid.UUID, token: str = Query(...)) -> dict[str, Any]:
    """Starts the customer's deposit payment — reuses the SAME `quote_view`
    token as viewing/accepting the quote (tenant+quote bound, expiring; see
    module docstring). Deliberately does NOT accept success_url/cancel_url
    from the unauthenticated caller (unlike the staff-only, authenticated
    `finance.create_quote_deposit_checkout_session` tool) — both redirect
    URLs are built server-side from FRONTEND_BASE_URL to close off an
    open-redirect vector on a surface with no login."""
    tenant_id = _resolve_token(quote_id, token)
    settings = get_settings()
    success_url = f"{settings.FRONTEND_BASE_URL}/quotes/view/{quote_id}?token={token}&deposit=success"
    cancel_url = f"{settings.FRONTEND_BASE_URL}/quotes/view/{quote_id}?token={token}&deposit=cancelled"
    try:
        result = await _deposit_service().create_deposit_checkout_session(
            tenant_id, quote_id, success_url=success_url, cancel_url=cancel_url,
        )
    except DepositQuoteNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quote not found") from exc
    except (DepositNotRequiredError, InvalidDepositStateError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except StripeNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Payments are not configured") from exc
    except DepositCheckoutError as exc:
        # Never leak the underlying Stripe error message to an
        # unauthenticated caller (mirrors app/api/v1/webhooks.py never
        # trusting/echoing provider internals past the boundary).
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Payment provider error") from exc
    return {"checkout_url": result.checkout_url}
