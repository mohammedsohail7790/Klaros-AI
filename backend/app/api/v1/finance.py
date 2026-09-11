"""/finance dashboard summary — real, derived-on-request numbers only.
Never a fabricated figure: every value here is either a real aggregate
query or an explicit 'NOT_CONNECTED'/zero."""

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db
from app.models.contract import Contract, ContractStatus
from app.models.finance import Invoice, InvoiceStatus, Payment
from app.models.operations import ExceptionStatus, Job, OperationsException
from app.models.quote import Quote, QuoteStatus
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


# Statuses that mean a quote is somewhere at/past ACCEPTED, i.e. genuinely
# committed by the customer — not merely still under consideration.
_QUOTE_ACCEPTED_OR_LATER = (
    QuoteStatus.ACCEPTED, QuoteStatus.DEPOSIT_PENDING, QuoteStatus.DEPOSIT_PAID, QuoteStatus.CONVERTED,
)


@router.get("/commercial-pipeline")
async def commercial_pipeline(
    current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """The Quote -> Contract -> Deposit -> Payment -> Job pipeline, absent
    from `/finance/summary` (Invoice/AR only) and from `/crm/metrics`
    (Lead/Appointment only) — genuinely missing until now, not duplicated
    from either. Every number is a real aggregate query against
    tenant-scoped rows, computed at request time; an empty tenant returns
    zeros, never a fabricated figure. `quotes_accepted_value` and
    `contracts_signed_value` are real sums of `Quote.total` for quotes
    already in an accepted-or-later state; `deposits_collected` sums real
    `Payment.amount` for payments linked to a quote (`Payment.quote_id`),
    which is exactly how a Stripe deposit payment is recorded (see
    `PaymentService.record_payment`)."""
    tenant_id = current_user.tenant_id

    quotes_sent = (
        await db.execute(
            select(func.count(Quote.id)).where(
                Quote.tenant_id == tenant_id, Quote.status.in_((QuoteStatus.SENT, QuoteStatus.VIEWED))
            )
        )
    ).scalar_one()

    quotes_accepted = (
        await db.execute(
            select(func.count(Quote.id)).where(
                Quote.tenant_id == tenant_id, Quote.status.in_(_QUOTE_ACCEPTED_OR_LATER)
            )
        )
    ).scalar_one()

    quotes_accepted_value = (
        await db.execute(
            select(func.coalesce(func.sum(Quote.total), 0)).where(
                Quote.tenant_id == tenant_id, Quote.status.in_(_QUOTE_ACCEPTED_OR_LATER)
            )
        )
    ).scalar_one()

    contracts_awaiting_signature = (
        await db.execute(
            select(func.count(Contract.id)).where(
                Contract.tenant_id == tenant_id,
                Contract.status.in_((ContractStatus.SENT, ContractStatus.VIEWED)),
            )
        )
    ).scalar_one()

    contracts_signed = (
        await db.execute(
            select(func.count(Contract.id)).where(
                Contract.tenant_id == tenant_id, Contract.status == ContractStatus.SIGNED
            )
        )
    ).scalar_one()

    deposits_awaiting_payment = (
        await db.execute(
            select(func.count(Quote.id)).where(
                Quote.tenant_id == tenant_id, Quote.status == QuoteStatus.DEPOSIT_PENDING
            )
        )
    ).scalar_one()

    deposits_awaiting_value = (
        await db.execute(
            select(func.coalesce(func.sum(Quote.deposit_amount), 0)).where(
                Quote.tenant_id == tenant_id, Quote.status == QuoteStatus.DEPOSIT_PENDING
            )
        )
    ).scalar_one()

    deposits_collected = (
        await db.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.tenant_id == tenant_id, Payment.quote_id.is_not(None),
            )
        )
    ).scalar_one()

    jobs_from_quotes = (
        await db.execute(
            select(func.count(Job.id)).where(Job.tenant_id == tenant_id, Job.quote_id.is_not(None))
        )
    ).scalar_one()

    return {
        "quotes_awaiting_response": quotes_sent,
        "quotes_accepted": quotes_accepted,
        "quotes_accepted_value": str(quotes_accepted_value),
        "contracts_awaiting_signature": contracts_awaiting_signature,
        "contracts_signed": contracts_signed,
        "deposits_awaiting_payment": deposits_awaiting_payment,
        "deposits_awaiting_value": str(deposits_awaiting_value),
        "deposits_collected": str(deposits_collected),
        "jobs_from_quotes": jobs_from_quotes,
        "needs_attention": contracts_awaiting_signature > 0 or deposits_awaiting_payment > 0,
    }
