import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from app.models.finance import Vendor, VendorBill
from app.models.rbac import Permission
from app.services.vendor_service import VendorBillNotFoundError, VendorNotFoundError, VendorService
from app.tools.base import ExecutionContext, Tool


def _vendor_to_dict(v: Vendor) -> dict[str, Any]:
    return {"id": str(v.id), "name": v.name, "email": v.email, "phone": v.phone, "status": v.status}


def _bill_to_dict(b: VendorBill) -> dict[str, Any]:
    return {
        "id": str(b.id), "vendor_id": str(b.vendor_id), "job_id": str(b.job_id) if b.job_id else None,
        "amount": str(b.amount), "due_date": b.due_date.isoformat(), "status": b.status,
    }


class CreateVendorInput(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None


class VendorOutput(BaseModel):
    vendor: dict[str, Any]


class CreateVendor(Tool):
    name = "finance.create_vendor"
    description = "Create a subcontractor/vendor record."
    input_schema = CreateVendorInput
    output_schema = VendorOutput
    required_permission = Permission.MANAGE_VENDORS

    def __init__(self, vendor_service: VendorService) -> None:
        self._vendor_service = vendor_service

    async def execute(self, input: CreateVendorInput, context: ExecutionContext) -> VendorOutput:
        vendor = await self._vendor_service.create_vendor(
            context.tenant_id, name=input.name, email=input.email, phone=input.phone
        )
        return VendorOutput(vendor=_vendor_to_dict(vendor))


class ListVendorsInput(BaseModel):
    pass


class ListVendorsOutput(BaseModel):
    vendors: list[dict[str, Any]]


class ListVendors(Tool):
    name = "finance.list_vendors"
    description = "List every subcontractor/vendor for this tenant."
    input_schema = ListVendorsInput
    output_schema = ListVendorsOutput
    required_permission = Permission.MANAGE_VENDORS

    def __init__(self, vendor_service: VendorService) -> None:
        self._vendor_service = vendor_service

    async def execute(self, input: ListVendorsInput, context: ExecutionContext) -> ListVendorsOutput:
        vendors = await self._vendor_service.list_vendors(context.tenant_id)
        return ListVendorsOutput(vendors=[_vendor_to_dict(v) for v in vendors])


class ListVendorBillsInput(BaseModel):
    vendor_id: uuid.UUID | None = None


class ListVendorBillsOutput(BaseModel):
    vendor_bills: list[dict[str, Any]]


class ListVendorBills(Tool):
    name = "finance.list_vendor_bills"
    description = "List vendor bills, optionally for a single vendor."
    input_schema = ListVendorBillsInput
    output_schema = ListVendorBillsOutput
    required_permission = Permission.MANAGE_VENDORS

    def __init__(self, vendor_service: VendorService) -> None:
        self._vendor_service = vendor_service

    async def execute(self, input: ListVendorBillsInput, context: ExecutionContext) -> ListVendorBillsOutput:
        bills = await self._vendor_service.list_vendor_bills(context.tenant_id, vendor_id=input.vendor_id)
        return ListVendorBillsOutput(vendor_bills=[_bill_to_dict(b) for b in bills])


class RecordVendorBillInput(BaseModel):
    vendor_id: uuid.UUID
    job_id: uuid.UUID | None = None
    amount: Decimal
    due_date: date


class VendorBillOutput(BaseModel):
    vendor_bill: dict[str, Any]


class RecordVendorBill(Tool):
    name = "finance.record_vendor_bill"
    description = "Record a subcontractor bill; if linked to a job, feeds a SUBCONTRACTOR JobCost row."
    input_schema = RecordVendorBillInput
    output_schema = VendorBillOutput
    required_permission = Permission.MANAGE_VENDORS

    def __init__(self, vendor_service: VendorService) -> None:
        self._vendor_service = vendor_service

    async def execute(self, input: RecordVendorBillInput, context: ExecutionContext) -> VendorBillOutput:
        try:
            bill = await self._vendor_service.record_bill(
                context.tenant_id, vendor_id=input.vendor_id, job_id=input.job_id,
                amount=input.amount, due_date=input.due_date,
            )
        except VendorNotFoundError as e:
            raise ValueError(str(e)) from e
        return VendorBillOutput(vendor_bill=_bill_to_dict(bill))


class RecordPayoutInput(BaseModel):
    vendor_id: uuid.UUID
    bill_id: uuid.UUID


class PayoutOutput(BaseModel):
    payout_id: str
    status: str
    external_reference: str | None


class RecordPayout(Tool):
    name = "finance.record_payout"
    description = "Pay a vendor bill via the internal test payout provider (not a real payment rail)."
    input_schema = RecordPayoutInput
    output_schema = PayoutOutput
    required_permission = Permission.MANAGE_VENDORS

    def __init__(self, vendor_service: VendorService) -> None:
        self._vendor_service = vendor_service

    async def execute(self, input: RecordPayoutInput, context: ExecutionContext) -> PayoutOutput:
        try:
            payout = await self._vendor_service.record_payout(
                context.tenant_id, vendor_id=input.vendor_id, bill_id=input.bill_id
            )
        except VendorBillNotFoundError as e:
            raise ValueError(str(e)) from e
        return PayoutOutput(
            payout_id=str(payout.id), status=payout.status, external_reference=payout.external_reference
        )
