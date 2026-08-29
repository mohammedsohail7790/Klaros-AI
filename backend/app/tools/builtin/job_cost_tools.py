import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from app.models.finance import JobCost
from app.models.rbac import Permission
from app.services.job_costing_service import JobCostingService, JobNotFoundError
from app.tools.base import ExecutionContext, Tool


def _cost_to_dict(c: JobCost) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "job_id": str(c.job_id),
        "category": c.category,
        "description": c.description,
        "quantity": str(c.quantity),
        "unit_cost": str(c.unit_cost),
        "total_cost": str(c.total_cost),
        "source": c.source,
    }


class RecordJobCostInput(BaseModel):
    job_id: uuid.UUID
    category: str
    description: str | None = None
    quantity: Decimal = Decimal("1")
    unit_cost: Decimal


class JobCostOutput(BaseModel):
    job_cost: dict[str, Any]


class RecordJobCost(Tool):
    name = "finance.record_job_cost"
    description = "Record an actual job cost (labor/material/subcontractor/travel/equipment/other) and recompute the job's actual margin."
    input_schema = RecordJobCostInput
    output_schema = JobCostOutput
    required_permission = Permission.MANAGE_JOB_COSTS

    def __init__(self, job_costing_service: JobCostingService) -> None:
        self._job_costing_service = job_costing_service

    async def execute(self, input: RecordJobCostInput, context: ExecutionContext) -> JobCostOutput:
        try:
            cost = await self._job_costing_service.record_cost(
                context.tenant_id,
                job_id=input.job_id,
                category=input.category,
                description=input.description,
                quantity=input.quantity,
                unit_cost=input.unit_cost,
            )
        except JobNotFoundError as e:
            raise ValueError(str(e)) from e
        return JobCostOutput(job_cost=_cost_to_dict(cost))


class SyncMaterialCostsInput(BaseModel):
    job_id: uuid.UUID


class SyncMaterialCostsOutput(BaseModel):
    created: int


class SyncMaterialCosts(Tool):
    name = "finance.sync_material_costs"
    description = "Create JobCost rows from job materials that have an actual_unit_cost but no cost row yet."
    input_schema = SyncMaterialCostsInput
    output_schema = SyncMaterialCostsOutput
    required_permission = Permission.MANAGE_JOB_COSTS

    def __init__(self, job_costing_service: JobCostingService) -> None:
        self._job_costing_service = job_costing_service

    async def execute(self, input: SyncMaterialCostsInput, context: ExecutionContext) -> SyncMaterialCostsOutput:
        created = await self._job_costing_service.sync_material_costs(context.tenant_id, input.job_id)
        return SyncMaterialCostsOutput(created=created)
