"""Warranty tracking tools. See app/services/warranty_service.py."""

import uuid
from datetime import date
from typing import Any

from pydantic import BaseModel

from app.models.rbac import Permission
from app.models.retention import Warranty
from app.services.warranty_service import WarrantyNotFoundError, WarrantyService
from app.tools.base import ExecutionContext, Tool


def _warranty_to_dict(w: Warranty) -> dict[str, Any]:
    return {
        "id": str(w.id),
        "customer_id": str(w.customer_id),
        "job_id": str(w.job_id) if w.job_id else None,
        "item_description": w.item_description,
        "start_date": w.start_date.isoformat(),
        "expiry_date": w.expiry_date.isoformat(),
        "status": w.status,
        "last_checked_in_at": w.last_checked_in_at.isoformat() if w.last_checked_in_at else None,
        "notes": w.notes,
    }


class CreateWarrantyInput(BaseModel):
    customer_id: uuid.UUID
    job_id: uuid.UUID | None = None
    item_description: str
    start_date: date
    expiry_date: date
    notes: str | None = None


class WarrantyOutput(BaseModel):
    warranty: dict[str, Any]


class CreateWarranty(Tool):
    name = "retention.create_warranty"
    description = "Record a warranty coverage window on a completed job."
    input_schema = CreateWarrantyInput
    output_schema = WarrantyOutput
    required_permission = Permission.MANAGE_RETENTION

    def __init__(self, warranty_service: WarrantyService) -> None:
        self._warranty_service = warranty_service

    async def execute(self, input: CreateWarrantyInput, context: ExecutionContext) -> WarrantyOutput:
        w = await self._warranty_service.create_warranty(
            context.tenant_id,
            customer_id=input.customer_id, job_id=input.job_id, item_description=input.item_description,
            start_date=input.start_date, expiry_date=input.expiry_date, notes=input.notes,
        )
        return WarrantyOutput(warranty=_warranty_to_dict(w))


class ListWarrantiesInput(BaseModel):
    customer_id: uuid.UUID | None = None
    status: str | None = None


class ListWarrantiesOutput(BaseModel):
    warranties: list[dict[str, Any]]


class ListWarranties(Tool):
    name = "retention.list_warranties"
    description = "List warranty coverage windows, optionally filtered by customer or status."
    input_schema = ListWarrantiesInput
    output_schema = ListWarrantiesOutput
    required_permission = Permission.READ_RETENTION

    def __init__(self, warranty_service: WarrantyService) -> None:
        self._warranty_service = warranty_service

    async def execute(self, input: ListWarrantiesInput, context: ExecutionContext) -> ListWarrantiesOutput:
        rows = await self._warranty_service.list_warranties(
            context.tenant_id, customer_id=input.customer_id, status=input.status
        )
        return ListWarrantiesOutput(warranties=[_warranty_to_dict(w) for w in rows])


class CheckInWarrantyInput(BaseModel):
    warranty_id: uuid.UUID
    notes: str | None = None


class CheckInWarranty(Tool):
    name = "retention.check_in_warranty"
    description = "Record a warranty check-in — a retention touchpoint before coverage lapses."
    input_schema = CheckInWarrantyInput
    output_schema = WarrantyOutput
    required_permission = Permission.MANAGE_RETENTION

    def __init__(self, warranty_service: WarrantyService) -> None:
        self._warranty_service = warranty_service

    async def execute(self, input: CheckInWarrantyInput, context: ExecutionContext) -> WarrantyOutput:
        try:
            w = await self._warranty_service.check_in(context.tenant_id, input.warranty_id, notes=input.notes)
        except WarrantyNotFoundError as e:
            raise ValueError(str(e)) from e
        return WarrantyOutput(warranty=_warranty_to_dict(w))


class EmptyInput(BaseModel):
    pass


class DetectExpiringWarrantiesOutput(BaseModel):
    newly_expiring_soon: list[str]
    newly_expired: list[str]


class DetectExpiringWarranties(Tool):
    name = "retention.detect_expiring_warranties"
    description = "Find warranties within 30 days of expiry or past it, and flag them."
    input_schema = EmptyInput
    output_schema = DetectExpiringWarrantiesOutput
    required_permission = Permission.MANAGE_RETENTION

    def __init__(self, warranty_service: WarrantyService) -> None:
        self._warranty_service = warranty_service

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> DetectExpiringWarrantiesOutput:
        result = await self._warranty_service.detect_expiring(context.tenant_id)
        return DetectExpiringWarrantiesOutput(**result)
