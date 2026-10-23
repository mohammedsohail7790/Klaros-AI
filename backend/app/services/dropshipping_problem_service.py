"""Dropshipping store: a shopper tells the shop that something is wrong with what arrived, and staff decide.

The shopper uses the link on their own order page (the unguessable status token), so no account and no order number guessing is involved. They may report only an order that has
shipped or been delivered, within the window the owners set (DROPSHIPPING_PROBLEM_WINDOW_DAYS, counted from payment), and only one open or approved report per order. What they
write is stored for staff and never shown back to anyone else. Staff approve (which flags the order for a refund exactly as the existing "request a refund" action does, so Klaros
still moves no money: staff refund in Stripe) or reject with a note.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.db.session import set_tenant_context
from app.models.dropshipping import Order, OrderStatus, ProblemReport, ProblemStatus
from app.services.dropshipping_catalog_import import CatalogValidationError
from app.services.dropshipping_catalog_service import CatalogConflictError, CatalogNotFoundError, _audit
from app.services.dropshipping_order_service import DropshippingOrderService

REPORTABLE = frozenset({OrderStatus.SHIPPED.value, OrderStatus.DELIVERED.value})
MIN_LEN, MAX_LEN = 10, 1000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def can_report(order: Order, has_open_report: bool, now: datetime | None = None) -> bool:
    """Whether the order page should offer the form: shipped or delivered, paid within the window, and no report already open or approved."""
    now = now or _now()
    paid = _aware(order.paid_at)
    if has_open_report or order.status not in REPORTABLE or paid is None or order.personal_data_erased_at is not None:
        return False
    return now - paid <= timedelta(days=max(1, get_settings().DROPSHIPPING_PROBLEM_WINDOW_DAYS))


class DropshippingProblemService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def report(self, tenant_id: uuid.UUID, public_token: str, description: object, now: datetime | None = None) -> None:
        text = str(description or "").strip()
        if not (MIN_LEN <= len(text) <= MAX_LEN):
            raise CatalogValidationError([f"please describe the problem in {MIN_LEN} to {MAX_LEN} characters"])
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            order = (await session.execute(select(Order).where(Order.tenant_id == tenant_id, Order.public_token == public_token))).scalar_one_or_none()
            if order is None or order.personal_data_erased_at is not None:
                raise CatalogNotFoundError("Order not found")
            existing = (await session.execute(select(ProblemReport.id).where(
                ProblemReport.tenant_id == tenant_id, ProblemReport.order_id == order.id, ProblemReport.status.in_([ProblemStatus.PENDING.value, ProblemStatus.APPROVED.value])).limit(1))).first()
            if existing is not None:
                raise CatalogConflictError("This order already has a report. We will reply by email.")
            if not can_report(order, False, now):
                raise CatalogValidationError(["a problem can be reported once the order has shipped, and within " f"{get_settings().DROPSHIPPING_PROBLEM_WINDOW_DAYS} days of payment"])
            report = ProblemReport(tenant_id=tenant_id, order_id=order.id, description=text, status=ProblemStatus.PENDING.value)
            session.add(report)
            await session.flush()
            _audit(session, tenant_id, None, "dropshipping.problem.reported", "dropshipping_order", order.id, {"order_number": order.order_number, "report_id": str(report.id)})
            await session.commit()

    async def status_for_order(self, session, tenant_id: uuid.UUID, order_id: uuid.UUID) -> str | None:
        """The latest report's status for the shopper's order page, or None."""
        return (await session.execute(select(ProblemReport.status).where(ProblemReport.tenant_id == tenant_id, ProblemReport.order_id == order_id).order_by(ProblemReport.created_at.desc()).limit(1))).scalar_one_or_none()

    async def list(self, tenant_id: uuid.UUID, *, status: str | None = None, limit: int = 100) -> list[dict]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            stmt = select(ProblemReport, Order.order_number).join(Order, Order.id == ProblemReport.order_id).where(ProblemReport.tenant_id == tenant_id)
            if status:
                stmt = stmt.where(ProblemReport.status == status)
            rows = (await session.execute(stmt.order_by(ProblemReport.created_at).limit(max(1, min(int(limit), 200))))).all()
            return [{"id": str(r.id), "order_id": str(r.order_id), "order_number": number, "status": r.status, "description": r.description, "decision_note": r.decision_note,
                     "created_at": r.created_at.isoformat() if r.created_at else None, "decided_at": r.decided_at.isoformat() if r.decided_at else None} for r, number in rows]

    async def pending_count(self, tenant_id: uuid.UUID) -> int:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            return int((await session.execute(select(func.count()).select_from(ProblemReport).where(ProblemReport.tenant_id == tenant_id, ProblemReport.status == ProblemStatus.PENDING.value))).scalar_one() or 0)

    async def decide(self, tenant_id: uuid.UUID, report_id: uuid.UUID, *, approve: bool, note: object = None, actor_id: uuid.UUID | None = None) -> dict:
        note_s = str(note or "").strip()
        if not approve and not note_s:
            raise CatalogValidationError(["say why the report is not approved: the shopper is told"])
        if len(note_s) > 1000:
            raise CatalogValidationError(["the note is at most 1000 characters"])
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            report = (await session.execute(select(ProblemReport).where(ProblemReport.id == report_id, ProblemReport.tenant_id == tenant_id))).scalar_one_or_none()
            if report is None:
                raise CatalogNotFoundError("Report not found")
            if report.status != ProblemStatus.PENDING.value:
                raise CatalogConflictError(f"This report was already {report.status.lower()}.")
            order_id, description = report.order_id, report.description
        if approve:
            # the same flag as "request a refund": it refuses (OrderTransitionError) if the order can no longer be refunded, and then the report stays pending
            await DropshippingOrderService(self._session_factory).request_refund(tenant_id, order_id, reason=f"Shopper report approved: {description[:900]}", actor_id=actor_id)
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            report = (await session.execute(select(ProblemReport).where(ProblemReport.id == report_id, ProblemReport.tenant_id == tenant_id))).scalar_one()
            new_status = ProblemStatus.APPROVED.value if approve else ProblemStatus.REJECTED.value
            report.status = new_status
            report.decision_note, report.decided_at, report.decided_by = note_s or None, _now(), actor_id
            _audit(session, tenant_id, actor_id, "dropshipping.problem.approved" if approve else "dropshipping.problem.rejected", "dropshipping_order", order_id, {"report_id": str(report_id)})
            await session.commit()
        return {"id": str(report_id), "status": new_status}
