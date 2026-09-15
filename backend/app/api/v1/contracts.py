"""Staff-facing REST surface for the Contract domain — mirrors
`app/api/v1/quotes.py`'s exact pattern: read endpoints query the DB
directly (tenant-scoped), mutations go through the ToolRegistry via
`_call_tool` (`app/tools/builtin/contract_tools.py`), never bypassing
permission/policy/audit. The customer's own public view/sign/decline
happens through `app/api/v1/public_contracts.py`, not here — no
authenticated ExecutionContext exists for an unauthenticated customer,
the same reasoning already established for quotes.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.models.contract import Contract
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/contracts", tags=["contracts"])


def _contract_to_dict(c: Contract) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "contract_number": c.contract_number,
        "quote_id": str(c.quote_id),
        "customer_id": str(c.customer_id),
        "status": c.status,
        "content": c.content,
        "sent_at": c.sent_at.isoformat() if c.sent_at else None,
        "viewed_at": c.viewed_at.isoformat() if c.viewed_at else None,
        "decided_at": c.decided_at.isoformat() if c.decided_at else None,
        "signer_name": c.signer_name,
        "signer_email": c.signer_email,
        "decline_reason": c.decline_reason,
        "created_at": c.created_at.isoformat(),
        # Deliberately NEVER includes content_hash/tenant_id — internal
        # verification fields, not something a staff list/detail view needs.
    }


@router.get("")
async def list_contracts(
    status_filter: str | None = None,
    quote_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    query = select(Contract).where(Contract.tenant_id == current_user.tenant_id)
    if status_filter:
        query = query.where(Contract.status == status_filter)
    if quote_id:
        query = query.where(Contract.quote_id == quote_id)
    if customer_id:
        query = query.where(Contract.customer_id == customer_id)
    query = query.order_by(Contract.created_at.desc())
    rows = (await db.execute(query)).scalars().all()
    return {"contracts": [_contract_to_dict(c) for c in rows]}


@router.get("/{contract_id}")
async def get_contract(
    contract_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    contract = await db.get(Contract, contract_id)
    if contract is None or contract.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contract not found")
    return _contract_to_dict(contract)


async def _call_tool(tool_name: str, payload: dict, current_user: CurrentUser, registry: ToolRegistry) -> dict:
    try:
        output = await registry.execute(tool_name, payload, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/{contract_id}/send")
async def send_contract(
    contract_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _call_tool("contracts.send_contract", {"contract_id": str(contract_id)}, current_user, registry)


@router.post("/detect-pending")
async def detect_pending_contracts(
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _call_tool("contracts.detect_pending", {}, current_user, registry)
