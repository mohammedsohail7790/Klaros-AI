"""/finance dashboard summary — real, derived-on-request numbers only.
Never a fabricated figure: every value here is either a real aggregate
query or an explicit 'NOT_CONNECTED'/zero."""

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db
from app.models.finance import Invoice, InvoiceStatus
from app.models.operations import ExceptionStatus, OperationsException
from app.services.ar_service import OPEN_STATUSES

router = APIRouter(prefix="/finance", tags=["finance"])

FINANCE_EXCEPTION_TYPES = (
    "INVOICE_OVERDUE", "PAYMENT_FAILED", "HIGH_AR", "MARGIN_LEAK",
    "UNAPPROVED_REFUND", "UNUSUAL_DISCOUNT", "CASH_SHORTFALL",
    "VENDOR_BILL_OVERDUE", "FORECAST_RISK",
)


@router.get("/summary")
async def finance_summary(
    current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    tenant_id = current_user.tenant_id

    total_ar = (
        await db.execute(
            select(func.coalesce(func.sum(Invoice.amount_due), 0)).where(
                Invoice.tenant_id == tenant_id, Invoice.status.in_(OPEN_STATUSES)
            )
        )
    ).scalar_one()

    overdue_count = (
        await db.execute(
            select(func.count(Invoice.id)).where(
                Invoice.tenant_id == tenant_id, Invoice.status == InvoiceStatus.OVERDUE
            )
        )
    ).scalar_one()

    pending_approval_count = (
        await db.execute(
            select(func.count(Invoice.id)).where(
                Invoice.tenant_id == tenant_id, Invoice.status == InvoiceStatus.PENDING_APPROVAL
            )
        )
    ).scalar_one()

    draft_count = (
        await db.execute(
            select(func.count(Invoice.id)).where(Invoice.tenant_id == tenant_id, Invoice.status == InvoiceStatus.DRAFT)
        )
    ).scalar_one()

    paid_this_period = (
        await db.execute(
            select(func.coalesce(func.sum(Invoice.amount_paid), 0)).where(
                Invoice.tenant_id == tenant_id, Invoice.status == InvoiceStatus.PAID
            )
        )
    ).scalar_one()

    open_finance_exceptions = (
        await db.execute(
            select(func.count(OperationsException.id)).where(
                OperationsException.tenant_id == tenant_id,
                OperationsException.status == ExceptionStatus.OPEN,
                OperationsException.type.in_(FINANCE_EXCEPTION_TYPES),
            )
        )
    ).scalar_one()

    return {
        "total_ar": str(total_ar),
        "overdue_invoice_count": overdue_count,
        "pending_approval_invoice_count": pending_approval_count,
        "draft_invoice_count": draft_count,
        "total_paid": str(paid_this_period),
        "open_finance_exception_count": open_finance_exceptions,
        "needs_attention": open_finance_exceptions > 0 or overdue_count > 0,
    }
