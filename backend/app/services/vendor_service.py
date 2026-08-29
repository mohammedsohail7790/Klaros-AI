"""section 19: vendors/subcontractor costs. `VendorBill` feeds job costing
(a SUBCONTRACTOR `JobCost` row is created alongside an APPROVED bill so the
job's actual cost reflects it — see `record_bill`). Payouts use the
internal test payout provider only; no real payment rail is connected.
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.finance import (
    Payout,
    PayoutStatus,
    Vendor,
    VendorBill,
    VendorBillStatus,
    VendorStatus,
)
from app.services.job_costing_service import JobCostingService


class VendorNotFoundError(Exception):
    pass


class VendorBillNotFoundError(Exception):
    pass


class VendorService:
    def __init__(self, session_factory: async_sessionmaker, job_costing: JobCostingService) -> None:
        self._session_factory = session_factory
        self._job_costing = job_costing

    async def create_vendor(self, tenant_id: uuid.UUID, *, name: str, email: str | None, phone: str | None) -> Vendor:
        async with self._session_factory() as session:
            vendor = Vendor(tenant_id=tenant_id, name=name, email=email, phone=phone, status=VendorStatus.ACTIVE)
            session.add(vendor)
            await session.commit()
            await session.refresh(vendor)
        return vendor

    async def record_bill(
        self,
        tenant_id: uuid.UUID,
        *,
        vendor_id: uuid.UUID,
        job_id: uuid.UUID | None,
        amount: Decimal,
        due_date: date,
    ) -> VendorBill:
        async with self._session_factory() as session:
            vendor = await session.get(Vendor, vendor_id)
            if vendor is None or vendor.tenant_id != tenant_id:
                raise VendorNotFoundError("Vendor not found")

            bill = VendorBill(
                tenant_id=tenant_id,
                vendor_id=vendor_id,
                job_id=job_id,
                amount=amount,
                due_date=due_date,
                status=VendorBillStatus.APPROVED,
            )
            session.add(bill)
            await session.commit()
            await session.refresh(bill)

        if job_id:
            await self._job_costing.record_cost(
                tenant_id,
                job_id=job_id,
                category="SUBCONTRACTOR",
                description=f"Vendor bill {bill.id} ({vendor.name})",
                quantity=Decimal("1"),
                unit_cost=amount,
                source="vendor_bill",
                vendor_id=vendor_id,
            )
        return bill

    async def record_payout(self, tenant_id: uuid.UUID, *, vendor_id: uuid.UUID, bill_id: uuid.UUID) -> Payout:
        async with self._session_factory() as session:
            bill = await session.get(VendorBill, bill_id)
            if bill is None or bill.tenant_id != tenant_id:
                raise VendorBillNotFoundError("Vendor bill not found")

            payout = Payout(
                tenant_id=tenant_id,
                vendor_id=vendor_id,
                job_id=bill.job_id,
                amount=bill.amount,
                status=PayoutStatus.PAID,
                provider="internal_test_payout",
                external_reference=f"test-payout-{uuid.uuid4().hex[:12]}",
            )
            session.add(payout)
            bill.status = VendorBillStatus.PAID
            await session.commit()
            await session.refresh(payout)
        return payout
