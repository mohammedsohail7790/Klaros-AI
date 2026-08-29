import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db
from app.models.finance import CollectionAction, Invoice
from app.services.ar_service import ARService
from app.services.collection_service import CollectionService

router = APIRouter(prefix="/ar", tags=["ar"])


def _services() -> tuple[ARService, CollectionService]:
    from app.communications.factory import get_communication_provider
    from app.db.session import async_session_maker
    from app.events.factory import get_event_bus
    from app.services.exception_service import ExceptionService

    bus = get_event_bus()
    exception_service = ExceptionService(async_session_maker, bus)
    collection_service = CollectionService(async_session_maker, get_communication_provider(async_session_maker))
    ar_service = ARService(async_session_maker, exception_service, collection_service)
    return ar_service, collection_service


@router.get("/aging")
async def get_aging(current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    ar_service, _ = _services()
    summary = await ar_service.aging_summary(current_user.tenant_id)
    return {
        "current": str(summary.current),
        "days_1_30": str(summary.days_1_30),
        "days_31_60": str(summary.days_31_60),
        "days_61_90": str(summary.days_61_90),
        "days_90_plus": str(summary.days_90_plus),
        "total": str(summary.total),
    }


@router.get("/customers/{customer_id}/balance")
async def get_customer_balance(
    customer_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user)
) -> dict[str, Any]:
    ar_service, _ = _services()
    balance = await ar_service.customer_balance(current_user.tenant_id, customer_id)
    return {"customer_id": str(customer_id), "balance": str(balance)}


@router.post("/detect-overdue")
async def detect_overdue(current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    ar_service, _ = _services()
    ids = await ar_service.detect_overdue(current_user.tenant_id)
    return {"newly_overdue_invoice_ids": [str(i) for i in ids]}


@router.get("/collections")
async def list_collection_actions(
    current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(CollectionAction, Invoice.invoice_number)
            .join(Invoice, Invoice.id == CollectionAction.invoice_id)
            .where(CollectionAction.tenant_id == current_user.tenant_id)
            .order_by(CollectionAction.scheduled_for.desc())
        )
    ).all()
    return {
        "collection_actions": [
            {
                "id": str(a.id),
                "invoice_id": str(a.invoice_id),
                "invoice_number": number,
                "action_type": a.action_type,
                "scheduled_for": a.scheduled_for.isoformat(),
                "status": a.status,
                "attempt": a.attempt,
            }
            for a, number in rows
        ]
    }


@router.post("/collections/execute-due")
async def execute_due_collections(current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    _, collection_service = _services()
    ids = await collection_service.execute_due_actions(current_user.tenant_id)
    return {"executed_action_ids": [str(i) for i in ids]}
