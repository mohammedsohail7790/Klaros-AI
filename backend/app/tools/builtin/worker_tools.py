"""section 9: worker/technician resource management.

Not named explicitly in the spec's tool list (which focuses on jobs), but
workers are a first-class tenant-owned resource that assignment depends on
— created/read through the same ToolRegistry pipeline as everything else,
not a bare CRUD table.
"""

import uuid
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.operations import Worker, WorkerStatus
from app.models.rbac import Permission
from app.tools.base import ExecutionContext, Tool


def _worker_to_dict(w: Worker) -> dict[str, Any]:
    return {
        "id": str(w.id),
        "name": w.name,
        "email": w.email,
        "phone": w.phone,
        "role": w.role,
        "status": w.status,
        "skills": w.skills,
        "service_types": w.service_types,
        "location": w.location,
        "active": w.active,
    }


class CreateWorkerInput(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None
    role: str | None = None
    skills: list[str] = []
    service_types: list[str] = []
    location: str | None = None


class WorkerOutput(BaseModel):
    worker: dict[str, Any]


class CreateWorker(Tool):
    name = "operations.create_worker"
    description = "Add a worker/technician resource."
    input_schema = CreateWorkerInput
    output_schema = WorkerOutput
    required_permission = Permission.ASSIGN_JOB

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(self, input: CreateWorkerInput, context: ExecutionContext) -> WorkerOutput:
        async with self._session_factory() as session:
            worker = Worker(
                tenant_id=context.tenant_id,
                name=input.name,
                email=input.email,
                phone=input.phone,
                role=input.role,
                skills=input.skills,
                service_types=input.service_types,
                location=input.location,
            )
            session.add(worker)
            await session.commit()
            await session.refresh(worker)
            return WorkerOutput(worker=_worker_to_dict(worker))


class ListWorkersInput(BaseModel):
    active_only: bool = True


class ListWorkersOutput(BaseModel):
    workers: list[dict[str, Any]]


class ListWorkers(Tool):
    name = "operations.list_workers"
    description = "List workers for this tenant."
    input_schema = ListWorkersInput
    output_schema = ListWorkersOutput
    required_permission = Permission.READ_JOBS

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(self, input: ListWorkersInput, context: ExecutionContext) -> ListWorkersOutput:
        async with self._session_factory() as session:
            query = select(Worker).where(Worker.tenant_id == context.tenant_id)
            if input.active_only:
                query = query.where(Worker.active.is_(True))
            workers = (await session.execute(query)).scalars().all()
            return ListWorkersOutput(workers=[_worker_to_dict(w) for w in workers])


class UpdateWorkerStatusInput(BaseModel):
    worker_id: uuid.UUID
    status: str


class UpdateWorkerStatus(Tool):
    name = "operations.update_worker_status"
    description = "Update a worker's availability status."
    input_schema = UpdateWorkerStatusInput
    output_schema = WorkerOutput
    required_permission = Permission.ASSIGN_JOB

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(self, input: UpdateWorkerStatusInput, context: ExecutionContext) -> WorkerOutput:
        try:
            WorkerStatus(input.status)
        except ValueError:
            raise ValueError(f"Invalid worker status: {input.status}") from None

        async with self._session_factory() as session:
            worker = await session.get(Worker, input.worker_id)
            if worker is None or worker.tenant_id != context.tenant_id:
                raise ValueError("Worker not found")
            worker.status = input.status
            await session.commit()
            await session.refresh(worker)
            return WorkerOutput(worker=_worker_to_dict(worker))
