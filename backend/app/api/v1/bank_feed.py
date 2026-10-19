"""`/api/v1/bank-feed`: the connected bank's accounts and transactions, a manual sync, and the owner's decision on each deposit.

Reading needs VIEW_FINANCIALS; sync needs MANAGE_COLLECTIONS (same as the crew's finance sweeps); confirming or ignoring a deposit needs RECORD_PAYMENT. Everything is scoped to the
caller's business. A match only records a link between a deposit and an invoice; it never creates or settles a payment. Connecting the bank itself is the generic
`/integrations/connectors/plaid/*` flow.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from app.api.deps import CurrentUser, require_permission
from app.api.tool_deps_integrations import get_integration_connection_service
from app.db.session import async_session_maker
from app.models.rbac import Permission
from app.services.bank_feed_service import BankFeedError, BankFeedService

router = APIRouter(prefix="/bank-feed", tags=["bank-feed"])


def get_bank_feed_service() -> BankFeedService:
    return BankFeedService(async_session_maker, get_integration_connection_service())


class BankAccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    mask: str | None
    type: str | None
    subtype: str | None
    current_balance: Decimal | None
    currency: str | None
    last_synced_at: datetime | None


class BankAccountsResponse(BaseModel):
    connected: bool
    connection_status: str
    connection_error: str | None
    last_sync_at: datetime | None
    last_sync_status: str | None  # ok | product_not_ready | login_required | error
    last_sync_error: str | None
    accounts: list[BankAccountResponse]


class BankTransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    bank_account_id: uuid.UUID
    posted_date: date
    amount: Decimal  # always positive; see `direction`
    currency: str
    description: str
    merchant: str | None
    pending: bool
    direction: str  # IN (deposit) | OUT (spend)
    match_status: str  # UNMATCHED | SUGGESTED | MATCHED | IGNORED
    matched_entity_type: str | None
    matched_entity_id: uuid.UUID | None
    match_method: str | None  # auto | manual
    match_candidates: list[dict[str, Any]] | None


class MatchRequest(BaseModel):
    entity_type: str = "invoice"
    entity_id: uuid.UUID


class SyncResponse(BaseModel):
    status: str  # not_connected | ok | product_not_ready | login_required | error
    added: int
    modified: int
    removed: int
    message: str
    reconcile: dict[str, int] | None = None


def _raise(exc: BankFeedError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/accounts", response_model=BankAccountsResponse)
async def accounts(current_user: CurrentUser = Depends(require_permission(Permission.VIEW_FINANCIALS)), service: BankFeedService = Depends(get_bank_feed_service)) -> Any:
    return await service.overview(current_user.tenant_id)


@router.get("/transactions", response_model=list[BankTransactionResponse])
async def transactions(
    status: str | None = Query(None, description="UNMATCHED | SUGGESTED | MATCHED | IGNORED"), limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
    current_user: CurrentUser = Depends(require_permission(Permission.VIEW_FINANCIALS)), service: BankFeedService = Depends(get_bank_feed_service),
) -> Any:
    try:
        return await service.list_transactions(current_user.tenant_id, status=status.upper() if status else None, limit=limit, offset=offset)
    except BankFeedError as exc:
        raise _raise(exc) from exc


@router.post("/sync", response_model=SyncResponse)
async def sync(current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_COLLECTIONS)), service: BankFeedService = Depends(get_bank_feed_service)) -> Any:
    return await service.sync_and_reconcile(current_user.tenant_id)


@router.post("/transactions/{transaction_id}/match", response_model=BankTransactionResponse)
async def match(
    transaction_id: uuid.UUID, body: MatchRequest, current_user: CurrentUser = Depends(require_permission(Permission.RECORD_PAYMENT)),
    service: BankFeedService = Depends(get_bank_feed_service),
) -> Any:
    try:
        return await service.match(current_user.tenant_id, transaction_id, entity_type=body.entity_type, entity_id=body.entity_id)
    except BankFeedError as exc:
        raise _raise(exc) from exc


@router.post("/transactions/{transaction_id}/ignore", response_model=BankTransactionResponse)
async def ignore(
    transaction_id: uuid.UUID, current_user: CurrentUser = Depends(require_permission(Permission.RECORD_PAYMENT)), service: BankFeedService = Depends(get_bank_feed_service),
) -> Any:
    try:
        return await service.ignore(current_user.tenant_id, transaction_id)
    except BankFeedError as exc:
        raise _raise(exc) from exc
