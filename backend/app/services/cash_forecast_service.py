"""section 20: 13-week cash forecast, generated from real data only —
open invoices (inflows) and open vendor bills (outflows) — with an
explicit HIGH/MEDIUM/LOW confidence label per item. Starting cash is
NOT_CONNECTED unless `Organization.manual_starting_cash` has been
explicitly set (MANUAL / INTERNAL TEST DATA only — no bank integration
exists). Never fabricates a balance.
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.finance import (
    CashForecast,
    CashForecastItem,
    ForecastConfidence,
    ForecastItemType,
    Invoice,
    InvoiceStatus,
    VendorBill,
    VendorBillStatus,
)
from app.models.organization import Organization

WEEKS = 13


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


class CashForecastService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def generate(self, tenant_id: uuid.UUID) -> CashForecast:
        today = date.today()
        horizon_end = today + timedelta(weeks=WEEKS)

        async with self._session_factory() as session:
            org = await session.get(Organization, tenant_id)
            starting_cash = org.manual_starting_cash if org else None
            starting_cash_source = "MANUAL_INTERNAL_TEST_DATA" if starting_cash is not None else "NOT_CONNECTED"

            forecast = CashForecast(
                tenant_id=tenant_id,
                generated_at=datetime.now(timezone.utc),
                starting_cash=starting_cash,
                starting_cash_source=starting_cash_source,
            )
            session.add(forecast)
            await session.flush()

            invoices = (
                await session.execute(
                    select(Invoice).where(
                        Invoice.tenant_id == tenant_id,
                        Invoice.status.in_(
                            (InvoiceStatus.SENT, InvoiceStatus.PARTIALLY_PAID, InvoiceStatus.OVERDUE)
                        ),
                        Invoice.due_date <= horizon_end,
                    )
                )
            ).scalars().all()

            for inv in invoices:
                confidence = (
                    ForecastConfidence.HIGH
                    if inv.status != InvoiceStatus.OVERDUE
                    else ForecastConfidence.LOW
                )
                session.add(
                    CashForecastItem(
                        tenant_id=tenant_id,
                        forecast_id=forecast.id,
                        week_start=_week_start(max(inv.due_date, today)),
                        type=ForecastItemType.INFLOW,
                        source=f"invoice:{inv.invoice_number}",
                        amount=inv.amount_due,
                        confidence=confidence,
                    )
                )

            bills = (
                await session.execute(
                    select(VendorBill).where(
                        VendorBill.tenant_id == tenant_id,
                        VendorBill.status.in_((VendorBillStatus.APPROVED, VendorBillStatus.OVERDUE)),
                        VendorBill.due_date <= horizon_end,
                    )
                )
            ).scalars().all()

            for bill in bills:
                confidence = (
                    ForecastConfidence.HIGH
                    if bill.status == VendorBillStatus.APPROVED
                    else ForecastConfidence.MEDIUM
                )
                session.add(
                    CashForecastItem(
                        tenant_id=tenant_id,
                        forecast_id=forecast.id,
                        week_start=_week_start(max(bill.due_date, today)),
                        type=ForecastItemType.OUTFLOW,
                        source=f"vendor_bill:{bill.id}",
                        amount=bill.amount,
                        confidence=confidence,
                    )
                )

            await session.commit()
            await session.refresh(forecast)

        return forecast

    async def weekly_projection(self, tenant_id: uuid.UUID, forecast_id: uuid.UUID) -> list[dict]:
        async with self._session_factory() as session:
            forecast = await session.get(CashForecast, forecast_id)
            items = (
                await session.execute(
                    select(CashForecastItem).where(
                        CashForecastItem.tenant_id == tenant_id, CashForecastItem.forecast_id == forecast_id
                    )
                )
            ).scalars().all()

        today = date.today()
        weeks = [_week_start(today) + timedelta(weeks=i) for i in range(WEEKS)]
        running = forecast.starting_cash if forecast and forecast.starting_cash is not None else None

        rows = []
        for week in weeks:
            week_items = [i for i in items if i.week_start == week]
            inflow = sum((i.amount for i in week_items if i.type == ForecastItemType.INFLOW), Decimal("0"))
            outflow = sum((i.amount for i in week_items if i.type == ForecastItemType.OUTFLOW), Decimal("0"))
            net = inflow - outflow
            if running is not None:
                running = running + net
            rows.append(
                {
                    "week_start": week.isoformat(),
                    "inflow": str(inflow),
                    "outflow": str(outflow),
                    "net": str(net),
                    "projected_balance": str(running) if running is not None else "NOT_CONNECTED",
                    "items": [
                        {
                            "type": i.type,
                            "source": i.source,
                            "amount": str(i.amount),
                            "confidence": i.confidence,
                        }
                        for i in week_items
                    ],
                }
            )
        return rows
