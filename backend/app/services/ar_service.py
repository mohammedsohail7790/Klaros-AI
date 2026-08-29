"""section 14: Accounts Receivable — a derived/computed service, not a
persisted table (per spec section 14's explicit allowance: AR is fully
derivable from `Invoice` rows with no sync-drift risk that way).

`detect_overdue` is the one place AR mutates anything: it flips SENT/
PARTIALLY_PAID invoices whose due_date has passed to OVERDUE, opens an
INVOICE_OVERDUE exception via the *existing* exception engine, and seeds
the invoice's first CollectionAction — all deterministic, called on demand
(API polling or a scheduled job), never a Temporal `workflow.sleep()`.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.finance import Invoice, InvoiceStatus
from app.models.operations import ExceptionType
from app.services.collection_service import CollectionService
from app.services.exception_service import ExceptionService

AGING_BUCKETS = ["current", "1_30", "31_60", "61_90", "90_plus"]

OPEN_STATUSES = (InvoiceStatus.SENT, InvoiceStatus.PARTIALLY_PAID, InvoiceStatus.OVERDUE)


@dataclass
class AgingSummary:
    current: Decimal = field(default_factory=lambda: Decimal("0"))
    days_1_30: Decimal = field(default_factory=lambda: Decimal("0"))
    days_31_60: Decimal = field(default_factory=lambda: Decimal("0"))
    days_61_90: Decimal = field(default_factory=lambda: Decimal("0"))
    days_90_plus: Decimal = field(default_factory=lambda: Decimal("0"))

    @property
    def total(self) -> Decimal:
        return self.current + self.days_1_30 + self.days_31_60 + self.days_61_90 + self.days_90_plus


def _bucket_for(due_date: date, as_of: date) -> str:
    days_overdue = (as_of - due_date).days
    if days_overdue <= 0:
        return "current"
    if days_overdue <= 30:
        return "1_30"
    if days_overdue <= 60:
        return "31_60"
    if days_overdue <= 90:
        return "61_90"
    return "90_plus"


class ARService:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        exception_service: ExceptionService,
        collection_service: CollectionService,
    ) -> None:
        self._session_factory = session_factory
        self._exception_service = exception_service
        self._collection_service = collection_service

    async def aging_summary(self, tenant_id: uuid.UUID, *, as_of: date | None = None) -> AgingSummary:
        as_of = as_of or date.today()
        summary = AgingSummary()
        async with self._session_factory() as session:
            invoices = (
                await session.execute(
                    select(Invoice).where(Invoice.tenant_id == tenant_id, Invoice.status.in_(OPEN_STATUSES))
                )
            ).scalars().all()

        for inv in invoices:
            bucket = _bucket_for(inv.due_date, as_of)
            if bucket == "current":
                summary.current += inv.amount_due
            elif bucket == "1_30":
                summary.days_1_30 += inv.amount_due
            elif bucket == "31_60":
                summary.days_31_60 += inv.amount_due
            elif bucket == "61_90":
                summary.days_61_90 += inv.amount_due
            else:
                summary.days_90_plus += inv.amount_due
        return summary

    async def customer_balance(self, tenant_id: uuid.UUID, customer_id: uuid.UUID) -> Decimal:
        async with self._session_factory() as session:
            invoices = (
                await session.execute(
                    select(Invoice).where(
                        Invoice.tenant_id == tenant_id,
                        Invoice.customer_id == customer_id,
                        Invoice.status.in_(OPEN_STATUSES),
                    )
                )
            ).scalars().all()
        return sum((inv.amount_due for inv in invoices), Decimal("0"))

    async def detect_overdue(self, tenant_id: uuid.UUID, *, as_of: date | None = None) -> list[uuid.UUID]:
        as_of = as_of or date.today()
        newly_overdue: list[uuid.UUID] = []

        async with self._session_factory() as session:
            candidates = (
                await session.execute(
                    select(Invoice).where(
                        Invoice.tenant_id == tenant_id,
                        Invoice.status.in_((InvoiceStatus.SENT, InvoiceStatus.PARTIALLY_PAID)),
                        Invoice.due_date < as_of,
                    )
                )
            ).scalars().all()

            for inv in candidates:
                inv.status = InvoiceStatus.OVERDUE
                newly_overdue.append(inv.id)
            await session.commit()

        for invoice_id in newly_overdue:
            async with self._session_factory() as session:
                inv = await session.get(Invoice, invoice_id)
            days_overdue = (as_of - inv.due_date).days
            await self._exception_service.create_exception(
                tenant_id,
                type=ExceptionType.INVOICE_OVERDUE,
                severity="HIGH" if days_overdue > 30 else "MEDIUM",
                entity_type="invoice",
                entity_id=invoice_id,
                description=f"Invoice {inv.invoice_number} is {days_overdue} days overdue (${inv.amount_due} due)",
                recommended_action="Review and initiate collections",
            )
            await self._collection_service.schedule_next_action(tenant_id, invoice_id, days_overdue=days_overdue)

        return newly_overdue
