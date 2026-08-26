import uuid

import pytest

from app.models.actor import ActorType
from app.models.communication import CommunicationLog
from app.models.rbac import Role
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id):
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=Role.OWNER)


async def test_create_exception_deduplicates(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    entity_id = uuid.uuid4()
    first = await tool_registry.execute(
        "operations.create_exception",
        {
            "type": "JOB_UNASSIGNED",
            "severity": "MEDIUM",
            "entity_type": "job",
            "entity_id": str(entity_id),
            "description": "no worker assigned",
        },
        _ctx(tenant_id),
    )
    second = await tool_registry.execute(
        "operations.create_exception",
        {
            "type": "JOB_UNASSIGNED",
            "severity": "MEDIUM",
            "entity_type": "job",
            "entity_id": str(entity_id),
            "description": "no worker assigned (again)",
        },
        _ctx(tenant_id),
    )
    assert first.exception["id"] == second.exception["id"]
    assert second.deduplicated is True


async def test_resolve_exception(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    created = await tool_registry.execute(
        "operations.create_exception",
        {
            "type": "JOB_BLOCKED",
            "severity": "HIGH",
            "entity_type": "job",
            "entity_id": str(uuid.uuid4()),
            "description": "blocked",
        },
        _ctx(tenant_id),
    )
    resolved = await tool_registry.execute(
        "operations.resolve_exception", {"exception_id": created.exception["id"]}, _ctx(tenant_id)
    )
    assert resolved.exception["status"] == "RESOLVED"
    assert resolved.exception["resolved_at"] is not None


async def test_delay_detection_flags_unassigned_scheduled_job(tool_registry, event_bus) -> None:
    from app.services.delay_detection_service import DelayDetectionService
    from app.services.exception_service import ExceptionService

    tenant_id = uuid.uuid4()
    customer = await tool_registry.execute("crm.create_customer", {"name": "C"}, _ctx(tenant_id))
    job = await tool_registry.execute(
        "operations.create_job", {"title": "Job", "customer_id": customer.customer["id"]}, _ctx(tenant_id)
    )
    await tool_registry.execute(
        "operations.schedule_job",
        {"job_id": job.job["id"], "start_time": "2026-10-10T09:00:00+00:00", "end_time": "2026-10-10T10:00:00+00:00"},
        _ctx(tenant_id),
    )

    exception_service = ExceptionService(event_bus.session_factory, event_bus)
    delay_service = DelayDetectionService(event_bus.session_factory, event_bus, exception_service)
    created = await delay_service.run(tenant_id)

    assert created["JOB_UNASSIGNED"] == 1

    exceptions = await tool_registry.execute(
        "operations.create_exception",
        {
            "type": "JOB_UNASSIGNED",
            "severity": "MEDIUM",
            "entity_type": "job",
            "entity_id": job.job["id"],
            "description": "dedupe check",
        },
        _ctx(tenant_id),
    )
    assert exceptions.deduplicated is True  # same exception already created by delay detection


async def test_job_dispatched_creates_communication_log(event_bus, tool_registry) -> None:
    from sqlalchemy import select

    from app.models.event import EventType

    tenant_id = uuid.uuid4()
    customer = await tool_registry.execute(
        "crm.create_customer", {"name": "Comms Customer", "email": "comms@example.com"}, _ctx(tenant_id)
    )
    job = await tool_registry.execute(
        "operations.create_job", {"title": "Job", "customer_id": customer.customer["id"]}, _ctx(tenant_id)
    )
    await tool_registry.execute(
        "operations.schedule_job",
        {"job_id": job.job["id"], "start_time": "2026-10-11T09:00:00+00:00", "end_time": "2026-10-11T10:00:00+00:00"},
        _ctx(tenant_id),
    )
    await tool_registry.execute("operations.dispatch_job", {"job_id": job.job["id"]}, _ctx(tenant_id))

    stats = await event_bus.process_pending(EventType.JOB_DISPATCHED)
    assert stats.succeeded >= 1

    async with event_bus.session_factory() as session:
        logs = (
            await session.execute(
                select(CommunicationLog).where(CommunicationLog.tenant_id == tenant_id)
            )
        ).scalars().all()
    assert any(log.recipient == "comms@example.com" for log in logs)
    assert all(log.provider == "internal_test_communication" for log in logs)
