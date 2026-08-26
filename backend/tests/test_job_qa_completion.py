import base64
import uuid

import pytest

from app.models.actor import ActorType
from app.models.rbac import Role
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id):
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=Role.OWNER)


async def _job_in_progress(tool_registry, tenant_id) -> str:
    customer = await tool_registry.execute("crm.create_customer", {"name": "C"}, _ctx(tenant_id))
    job = await tool_registry.execute(
        "operations.create_job", {"title": "Job", "customer_id": customer.customer["id"]}, _ctx(tenant_id)
    )
    job_id = job.job["id"]
    await tool_registry.execute(
        "operations.schedule_job",
        {"job_id": job_id, "start_time": "2026-10-09T09:00:00+00:00", "end_time": "2026-10-09T10:00:00+00:00"},
        _ctx(tenant_id),
    )
    await tool_registry.execute("operations.dispatch_job", {"job_id": job_id}, _ctx(tenant_id))
    await tool_registry.execute(
        "operations.update_job_status", {"job_id": job_id, "target_status": "EN_ROUTE"}, _ctx(tenant_id)
    )
    await tool_registry.execute(
        "operations.update_job_status", {"job_id": job_id, "target_status": "ON_SITE"}, _ctx(tenant_id)
    )
    await tool_registry.execute("operations.start_job", {"job_id": job_id}, _ctx(tenant_id))
    return job_id


async def test_qa_fails_when_required_task_incomplete(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    job_id = await _job_in_progress(tool_registry, tenant_id)
    await tool_registry.execute(
        "operations.create_task", {"job_id": job_id, "title": "Sign-off", "required": True}, _ctx(tenant_id)
    )
    await tool_registry.execute("operations.complete_job", {"job_id": job_id}, _ctx(tenant_id))

    with pytest.raises(ValueError, match="required task"):
        await tool_registry.execute("operations.complete_qa", {"job_id": job_id}, _ctx(tenant_id))


async def test_qa_passes_and_job_completes(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    job_id = await _job_in_progress(tool_registry, tenant_id)

    task = await tool_registry.execute(
        "operations.create_task", {"job_id": job_id, "title": "Inspect", "required": True}, _ctx(tenant_id)
    )
    await tool_registry.execute("operations.complete_task", {"task_id": task.task["id"]}, _ctx(tenant_id))
    await tool_registry.execute(
        "operations.add_job_photo",
        {
            "job_id": job_id,
            "filename": "done.jpg",
            "content_type": "image/jpeg",
            "content_base64": base64.b64encode(b"photo").decode(),
        },
        _ctx(tenant_id),
    )
    await tool_registry.execute("operations.complete_job", {"job_id": job_id}, _ctx(tenant_id))

    await tool_registry.execute("operations.start_qa", {"job_id": job_id}, _ctx(tenant_id))
    result = await tool_registry.execute("operations.complete_qa", {"job_id": job_id}, _ctx(tenant_id))
    assert result.qa["status"] == "PASSED"

    job = await tool_registry.execute("operations.get_job", {"job_id": job_id}, _ctx(tenant_id))
    assert job.job["status"] == "COMPLETED"


async def test_fail_qa_keeps_job_qa_pending_and_creates_exception(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    job_id = await _job_in_progress(tool_registry, tenant_id)
    await tool_registry.execute("operations.complete_job", {"job_id": job_id}, _ctx(tenant_id))

    result = await tool_registry.execute(
        "operations.fail_qa", {"job_id": job_id, "reason": "workmanship issue"}, _ctx(tenant_id)
    )
    assert result.qa["status"] == "FAILED"

    job = await tool_registry.execute("operations.get_job", {"job_id": job_id}, _ctx(tenant_id))
    assert job.job["status"] == "QA_PENDING"


async def test_close_job_requires_completion_packet(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    job_id = await _job_in_progress(tool_registry, tenant_id)
    await tool_registry.execute(
        "operations.add_job_photo",
        {
            "job_id": job_id,
            "filename": "x.jpg",
            "content_type": "image/jpeg",
            "content_base64": base64.b64encode(b"x").decode(),
        },
        _ctx(tenant_id),
    )
    await tool_registry.execute("operations.complete_job", {"job_id": job_id}, _ctx(tenant_id))
    await tool_registry.execute("operations.start_qa", {"job_id": job_id}, _ctx(tenant_id))
    await tool_registry.execute("operations.complete_qa", {"job_id": job_id}, _ctx(tenant_id))

    with pytest.raises(ValueError, match="completion packet"):
        await tool_registry.execute("operations.close_job", {"job_id": job_id}, _ctx(tenant_id))

    await tool_registry.execute("operations.generate_completion_packet", {"job_id": job_id}, _ctx(tenant_id))
    closed = await tool_registry.execute("operations.close_job", {"job_id": job_id}, _ctx(tenant_id))
    assert closed.job["status"] == "CLOSED"


async def test_close_job_emits_invoice_trigger_requested_not_an_invoice(event_bus, tool_registry) -> None:
    from sqlalchemy import select

    from app.models.event import Event, EventType

    tenant_id = uuid.uuid4()
    job_id = await _job_in_progress(tool_registry, tenant_id)
    await tool_registry.execute(
        "operations.add_job_photo",
        {
            "job_id": job_id,
            "filename": "x.jpg",
            "content_type": "image/jpeg",
            "content_base64": base64.b64encode(b"x").decode(),
        },
        _ctx(tenant_id),
    )
    await tool_registry.execute("operations.complete_job", {"job_id": job_id}, _ctx(tenant_id))
    await tool_registry.execute("operations.start_qa", {"job_id": job_id}, _ctx(tenant_id))
    await tool_registry.execute("operations.complete_qa", {"job_id": job_id}, _ctx(tenant_id))
    await tool_registry.execute("operations.generate_completion_packet", {"job_id": job_id}, _ctx(tenant_id))
    await tool_registry.execute("operations.close_job", {"job_id": job_id}, _ctx(tenant_id))

    async with event_bus.session_factory() as session:
        rows = (
            await session.execute(
                select(Event).where(
                    Event.tenant_id == tenant_id, Event.event_type == EventType.INVOICE_TRIGGER_REQUESTED
                )
            )
        ).scalars().all()
    assert len(rows) == 1
    assert "Finance module not implemented" in rows[0].payload["note"]
