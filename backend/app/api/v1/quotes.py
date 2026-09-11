import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.models.quote import Quote, QuoteLineItem
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/quotes", tags=["quotes"])


def _quote_to_dict(q: Quote) -> dict[str, Any]:
    return {
        "id": str(q.id),
        "quote_number": q.quote_number,
        "customer_id": str(q.customer_id),
        "lead_id": str(q.lead_id) if q.lead_id else None,
        "job_id": str(q.job_id) if q.job_id else None,
        "status": q.status,
        "currency": q.currency,
        "subtotal": str(q.subtotal),
        "tax": str(q.tax),
        "discount": str(q.discount),
        "total": str(q.total),
        "notes": q.notes,
        "terms": q.terms,
        "valid_until": q.valid_until.isoformat() if q.valid_until else None,
        "sent_at": q.sent_at.isoformat() if q.sent_at else None,
        "viewed_at": q.viewed_at.isoformat() if q.viewed_at else None,
        "decided_at": q.decided_at.isoformat() if q.decided_at else None,
        "decline_reason": q.decline_reason,
        "deposit_type": q.deposit_type,
        "deposit_value": str(q.deposit_value) if q.deposit_value is not None else None,
        "deposit_amount": str(q.deposit_amount) if q.deposit_amount is not None else None,
    }


def _line_item_to_dict(li: QuoteLineItem) -> dict[str, Any]:
    return {
        "id": str(li.id),
        "description": li.description,
        "quantity": str(li.quantity),
        "unit_price": str(li.unit_price),
        "discount": str(li.discount),
        "tax_rate": str(li.tax_rate),
        "line_total": str(li.line_total),
    }


@router.get("")
async def list_quotes(
    status_filter: str | None = None,
    customer_id: uuid.UUID | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    query = select(Quote).where(Quote.tenant_id == current_user.tenant_id)
    if status_filter:
        query = query.where(Quote.status == status_filter)
    if customer_id:
        query = query.where(Quote.customer_id == customer_id)
    query = query.order_by(Quote.created_at.desc())
    rows = (await db.execute(query)).scalars().all()
    return {"quotes": [_quote_to_dict(q) for q in rows]}


@router.get("/{quote_id}")
async def get_quote(
    quote_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    quote = await db.get(Quote, quote_id)
    if quote is None or quote.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quote not found")
    items = (
        await db.execute(
            select(QuoteLineItem)
            .where(QuoteLineItem.tenant_id == current_user.tenant_id, QuoteLineItem.quote_id == quote_id)
            .order_by(QuoteLineItem.sort_order)
        )
    ).scalars().all()
    result = _quote_to_dict(quote)
    result["line_items"] = [_line_item_to_dict(i) for i in items]
    return result


async def _call_tool(tool_name: str, payload: dict, current_user: CurrentUser, registry: ToolRegistry) -> dict:
    try:
        output = await registry.execute(tool_name, payload, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("")
async def create_quote_draft(
    body: dict,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _call_tool("quotes.create_quote_draft", body, current_user, registry)


@router.put("/{quote_id}")
async def update_quote_draft(
    quote_id: uuid.UUID,
    body: dict,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _call_tool("quotes.update_quote_draft", {**body, "quote_id": str(quote_id)}, current_user, registry)


@router.post("/{quote_id}/send")
async def send_quote(
    quote_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _call_tool("quotes.send_quote", {"quote_id": str(quote_id)}, current_user, registry)


@router.post("/detect-expired")
async def detect_expired_quotes(
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _call_tool("quotes.detect_expired_quotes", {}, current_user, registry)


@router.get("/{quote_id}/deposit")
async def get_quote_deposit_status(
    quote_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _call_tool(
        "finance.get_quote_deposit_status", {"quote_id": str(quote_id)}, current_user, registry
    )


@router.post("/{quote_id}/deposit/checkout")
async def create_quote_deposit_checkout(
    quote_id: uuid.UUID,
    body: dict,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _call_tool(
        "finance.create_quote_deposit_checkout_session",
        {**body, "quote_id": str(quote_id)},
        current_user,
        registry,
    )
