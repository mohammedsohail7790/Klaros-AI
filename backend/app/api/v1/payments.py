import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.models.finance import Payment
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/payments", tags=["payments"])


def _payment_to_dict(p: Payment) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "customer_id": str(p.customer_id),
        "amount": str(p.amount),
        "status": p.status,
        "payment_method": p.payment_method,
        "provider": p.provider,
        "external_id": p.external_id,
        "received_at": p.received_at.isoformat(),
    }


@router.get("")
async def list_payments(
    customer_id: uuid.UUID | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    query = select(Payment).where(Payment.tenant_id == current_user.tenant_id)
    if customer_id:
        query = query.where(Payment.customer_id == customer_id)
    query = query.order_by(Payment.received_at.desc())
    rows = (await db.execute(query)).scalars().all()
    return {"payments": [_payment_to_dict(p) for p in rows]}


@router.post("/test-payment")
async def record_test_payment(
    body: dict,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    """`body` = {customer_id, amount, allocations: [{invoice_id, amount}], payment_method?}
    — routed straight to finance.record_test_payment (INTERNAL TEST PAYMENT PROVIDER)."""
    try:
        output = await registry.execute("finance.record_test_payment", body, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")
