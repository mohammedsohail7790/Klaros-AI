import uuid
from typing import Any

from pydantic import BaseModel

from app.models.operations import JobMaterial, JobTask, PurchaseOrder, PurchaseOrderItem
from app.models.rbac import Permission
from app.services.material_service import MaterialService
from app.services.task_service import TaskService
from app.tools.base import ExecutionContext, Tool


def _task_to_dict(t: JobTask) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "job_id": str(t.job_id),
        "title": t.title,
        "description": t.description,
        "status": t.status,
        "required": t.required,
        "sort_order": t.sort_order,
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
    }


def _material_to_dict(m: JobMaterial) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "job_id": str(m.job_id),
        "name": m.name,
        "quantity": float(m.quantity),
        "unit": m.unit,
        "estimated_unit_cost": float(m.estimated_unit_cost) if m.estimated_unit_cost is not None else None,
        "supplier": m.supplier,
        "status": m.status,
    }


class CreateTaskInput(BaseModel):
    job_id: uuid.UUID
    title: str
    description: str | None = None
    required: bool = True
    assigned_to: uuid.UUID | None = None


class TaskOutput(BaseModel):
    task: dict[str, Any]


class CreateTask(Tool):
    name = "operations.create_task"
    description = "Add a checklist task to a job."
    input_schema = CreateTaskInput
    output_schema = TaskOutput
    required_permission = Permission.MANAGE_TASKS

    def __init__(self, task_service: TaskService) -> None:
        self._task_service = task_service

    async def execute(self, input: CreateTaskInput, context: ExecutionContext) -> TaskOutput:
        task = await self._task_service.create_task(
            context.tenant_id,
            input.job_id,
            title=input.title,
            description=input.description,
            required=input.required,
            assigned_to=input.assigned_to,
        )
        return TaskOutput(task=_task_to_dict(task))


class CompleteTaskInput(BaseModel):
    task_id: uuid.UUID
    skip: bool = False


class CompleteTask(Tool):
    name = "operations.complete_task"
    description = "Mark a task completed (or skipped)."
    input_schema = CompleteTaskInput
    output_schema = TaskOutput
    required_permission = Permission.MANAGE_TASKS

    def __init__(self, task_service: TaskService) -> None:
        self._task_service = task_service

    async def execute(self, input: CompleteTaskInput, context: ExecutionContext) -> TaskOutput:
        task = await self._task_service.complete_task(
            context.tenant_id, input.task_id, completed_by=context.actor_id, skip=input.skip
        )
        return TaskOutput(task=_task_to_dict(task))


class AddMaterialInput(BaseModel):
    job_id: uuid.UUID
    name: str
    quantity: float = 1
    unit: str | None = None
    description: str | None = None
    estimated_unit_cost: float | None = None
    supplier: str | None = None


class MaterialOutput(BaseModel):
    material: dict[str, Any]


class AddMaterial(Tool):
    name = "operations.add_material"
    description = "Record a material required for a job."
    input_schema = AddMaterialInput
    output_schema = MaterialOutput
    required_permission = Permission.MANAGE_MATERIALS

    def __init__(self, material_service: MaterialService) -> None:
        self._material_service = material_service

    async def execute(self, input: AddMaterialInput, context: ExecutionContext) -> MaterialOutput:
        material = await self._material_service.add_material(
            context.tenant_id,
            input.job_id,
            name=input.name,
            quantity=input.quantity,
            unit=input.unit,
            description=input.description,
            estimated_unit_cost=input.estimated_unit_cost,
            supplier=input.supplier,
        )
        return MaterialOutput(material=_material_to_dict(material))


class CreatePODraftInput(BaseModel):
    job_id: uuid.UUID
    supplier: str | None = None


class CreatePODraftOutput(BaseModel):
    purchase_order: dict[str, Any]
    items: list[dict[str, Any]]


class CreatePurchaseOrderDraft(Tool):
    name = "operations.create_purchase_order_draft"
    description = "Generate a DRAFT purchase order from a job's REQUIRED materials. No supplier is connected."
    input_schema = CreatePODraftInput
    output_schema = CreatePODraftOutput
    required_permission = Permission.MANAGE_MATERIALS

    def __init__(self, material_service: MaterialService) -> None:
        self._material_service = material_service

    async def execute(self, input: CreatePODraftInput, context: ExecutionContext) -> CreatePODraftOutput:
        po, items = await self._material_service.create_purchase_order_draft(
            context.tenant_id, input.job_id, supplier=input.supplier
        )
        return CreatePODraftOutput(
            purchase_order=_po_to_dict(po),
            items=[_po_item_to_dict(i) for i in items],
        )


def _po_to_dict(po: PurchaseOrder) -> dict[str, Any]:
    return {"id": str(po.id), "job_id": str(po.job_id), "status": po.status, "supplier": po.supplier}


def _po_item_to_dict(i: PurchaseOrderItem) -> dict[str, Any]:
    return {
        "id": str(i.id),
        "name": i.name,
        "quantity": float(i.quantity),
        "unit": i.unit,
        "estimated_unit_cost": float(i.estimated_unit_cost) if i.estimated_unit_cost is not None else None,
    }
