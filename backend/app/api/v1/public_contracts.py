"""The public, UNAUTHENTICATED contract view/sign/decline endpoints —
mirrors `app/api/v1/public_quotes.py` exactly: trust boundary is the
signed `contract_view` token (`app/core/security.py::
create_contract_view_token`/`decode_contract_view_token`), `tenant_id`/
`contract_id` read ONLY from the verified token payload, the `{contract_id}`
path parameter checked for equality against the token's own `contract_id`,
never used as authority on its own.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.api.tool_deps import get_wired_event_bus
from app.core.rate_limit import path_param_and_ip_key, rate_limit
from app.core.security import TokenError, decode_contract_view_token
from app.db.session import async_session_maker
from app.events.bus import EventBus
from app.models.contract import Contract
from app.services.contract_service import (
    ContractNotFoundError,
    ContractService,
    InvalidContractTransitionError,
)

router = APIRouter(prefix="/public/contracts", tags=["public-contracts"])

_rate_limit_dependency = Depends(
    rate_limit(
        "public_contract",
        limit_setting="RATE_LIMIT_PUBLIC_CONTRACT_PER_MINUTE",
        window_seconds=60,
        key_func=path_param_and_ip_key("contract_id"),
    )
)


def _contract_to_dict(c: Contract) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "contract_number": c.contract_number,
        "status": c.status,
        "content": c.content,
        "sent_at": c.sent_at.isoformat() if c.sent_at else None,
        "viewed_at": c.viewed_at.isoformat() if c.viewed_at else None,
        "decided_at": c.decided_at.isoformat() if c.decided_at else None,
        "signer_name": c.signer_name,
        # Deliberately NEVER includes customer_id/tenant_id/quote_id/
        # content_hash/signer_email — served to an unauthenticated browser.
    }


def _resolve_token(contract_id: uuid.UUID, token: str) -> uuid.UUID:
    try:
        payload = decode_contract_view_token(token)
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid or expired link") from exc
    if payload.get("contract_id") != str(contract_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid or expired link")
    return uuid.UUID(payload["tenant_id"])


def _service(bus: EventBus) -> ContractService:
    return ContractService(async_session_maker, bus)


@router.get("/{contract_id}", dependencies=[_rate_limit_dependency])
async def view_contract(
    contract_id: uuid.UUID, token: str = Query(...), bus: EventBus = Depends(get_wired_event_bus)
) -> dict[str, Any]:
    tenant_id = _resolve_token(contract_id, token)
    try:
        contract = await _service(bus).get_for_public_view(tenant_id, contract_id)
    except ContractNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contract not found") from exc
    return _contract_to_dict(contract)


class SignInput(BaseModel):
    signer_name: str
    signer_email: str | None = None


@router.post("/{contract_id}/sign", dependencies=[_rate_limit_dependency])
async def sign_contract(
    contract_id: uuid.UUID, body: SignInput, token: str = Query(...), bus: EventBus = Depends(get_wired_event_bus)
) -> dict[str, Any]:
    tenant_id = _resolve_token(contract_id, token)
    try:
        contract = await _service(bus).sign(
            tenant_id, contract_id, signer_name=body.signer_name, signer_email=body.signer_email,
        )
    except ContractNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contract not found") from exc
    except InvalidContractTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _contract_to_dict(contract)


class DeclineInput(BaseModel):
    reason: str | None = None


@router.post("/{contract_id}/decline", dependencies=[_rate_limit_dependency])
async def decline_contract(
    contract_id: uuid.UUID, body: DeclineInput, token: str = Query(...), bus: EventBus = Depends(get_wired_event_bus)
) -> dict[str, Any]:
    tenant_id = _resolve_token(contract_id, token)
    try:
        contract = await _service(bus).decline(tenant_id, contract_id, reason=body.reason)
    except ContractNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contract not found") from exc
    except InvalidContractTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _contract_to_dict(contract)
