"""section 15: job task/checklist system."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.operations import Job, JobTask, TaskStatus


class JobNotFoundError(Exception):
    pass


class TaskNotFoundError(Exception):
    pass


class TaskService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def create_task(
        self,
        tenant_id: uuid.UUID,
        job_id: uuid.UUID,
        *,
        title: str,
        description: str | None = None,
        required: bool = True,
        assigned_to: uuid.UUID | None = None,
    ) -> JobTask:
        async with self._session_factory() as session:
            job = await session.get(Job, job_id)
            if job is None or job.tenant_id != tenant_id:
                raise JobNotFoundError("Job not found")

            next_order = (
                await session.execute(
                    select(func.count(JobTask.id)).where(
                        JobTask.tenant_id == tenant_id, JobTask.job_id == job_id
                    )
                )
            ).scalar_one()

            task = JobTask(
                tenant_id=tenant_id,
                job_id=job_id,
                title=title,
                description=description,
                status=TaskStatus.PENDING,
                required=required,
                assigned_to=assigned_to,
                sort_order=next_order,
            )
            session.add(task)
            await session.commit()
            await session.refresh(task)
            return task

    async def complete_task(
        self, tenant_id: uuid.UUID, task_id: uuid.UUID, *, completed_by: uuid.UUID | None, skip: bool = False
    ) -> JobTask:
        async with self._session_factory() as session:
            task = await session.get(JobTask, task_id)
            if task is None or task.tenant_id != tenant_id:
                raise TaskNotFoundError("Task not found")
            task.status = TaskStatus.SKIPPED if skip else TaskStatus.COMPLETED
            task.completed_at = datetime.now(timezone.utc)
            task.completed_by = completed_by
            await session.commit()
            await session.refresh(task)
            return task
