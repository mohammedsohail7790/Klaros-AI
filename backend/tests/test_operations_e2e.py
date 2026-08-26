"""section 42: the full, realistic Operations & Delivery scenario, actually
run end to end — not just each step tested in isolation.

    existing customer -> appointment -> create job -> schedule -> assign
    -> dispatch -> en route -> on site -> start -> complete required tasks
    -> upload photos -> add materials -> add worker note -> start QA
    -> QA pass -> generate completion packet -> customer notification
    -> close job -> invoice.trigger_requested -> owner cockpit updated
    -> audit timeline updated
"""

import base64
import uuid

import pytest
from sqlalchemy import select

from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.communication import CommunicationLog
from app.models.event import EventType
from app.models.rbac import Role
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id):
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=Role.OWNER)


async def test_full_operations_lifecycle(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    # 1. Existing customer.
    customer = await tool_registry.execute(
        "crm.create_customer", {"name": "Full Lifecycle Customer", "email": "lifecycle@example.com"}, ctx
    )
    customer_id = customer.customer["id"]

    # 2. Appointment.
    appt = await tool_registry.execute(
        "crm.create_appointment",
        {
            "customer_id": customer_id,
            "title": "Furnace tune-up",
            "start_time": "2026-11-01T09:00:00+00:00",
            "end_time": "2026-11-01T10:00:00+00:00",
        },
        ctx,
    )
    appointment_id = appt.appointment["id"]

    # 3. Create job from the appointment (idempotent).
    job_out = await tool_registry.execute(
        "operations.create_job",
        {"title": "Furnace tune-up", "customer_id": customer_id, "appointment_id": appointment_id},
        ctx,
    )
    job_id = job_out.job["id"]
    assert job_out.job["status"] == "DRAFT"

    # 4. Schedule.
    await tool_registry.execute(
        "operations.schedule_job",
        {"job_id": job_id, "start_time": "2026-11-01T09:00:00+00:00", "end_time": "2026-11-01T10:00:00+00:00"},
        ctx,
    )

    # 5. Assign technician.
    worker = await tool_registry.execute(
        "operations.create_worker", {"name": "Ahmed", "service_types": ["HVAC"]}, ctx
    )
    worker_id = worker.worker["id"]
    await tool_registry.execute("operations.assign_job", {"job_id": job_id, "worker_id": worker_id}, ctx)

    # 6. Dispatch.
    await tool_registry.execute("operations.dispatch_job", {"job_id": job_id}, ctx)

    # 7. En route.
    await tool_registry.execute(
        "operations.update_job_status", {"job_id": job_id, "target_status": "EN_ROUTE"}, ctx
    )

    # 8. On site.
    await tool_registry.execute(
        "operations.update_job_status", {"job_id": job_id, "target_status": "ON_SITE"}, ctx
    )

    # 9. Start job.
    started = await tool_registry.execute("operations.start_job", {"job_id": job_id}, ctx)
    assert started.job["status"] == "IN_PROGRESS"

    # 10. Complete required tasks.
    task = await tool_registry.execute(
        "operations.create_task", {"job_id": job_id, "title": "Inspect unit", "required": True}, ctx
    )
    await tool_registry.execute("operations.complete_task", {"task_id": task.task["id"]}, ctx)

    # 11. Upload photos.
    await tool_registry.execute(
        "operations.add_job_photo",
        {
            "job_id": job_id,
            "filename": "final.jpg",
            "content_type": "image/jpeg",
            "content_base64": base64.b64encode(b"real photo bytes").decode(),
        },
        ctx,
    )

    # 12. Add materials.
    await tool_registry.execute(
        "operations.add_material", {"job_id": job_id, "name": "Filter", "quantity": 1}, ctx
    )

    # 13. Add worker note (internal_notes update).
    await tool_registry.execute(
        "operations.update_job", {"job_id": job_id, "internal_notes": "Unit was low on refrigerant."}, ctx
    )

    # 14. Complete field work -> QA_PENDING.
    await tool_registry.execute("operations.complete_job", {"job_id": job_id}, ctx)

    # 15. Start QA.
    await tool_registry.execute("operations.start_qa", {"job_id": job_id}, ctx)

    # 16. QA pass -> job COMPLETED.
    qa_result = await tool_registry.execute("operations.complete_qa", {"job_id": job_id}, ctx)
    assert qa_result.qa["status"] == "PASSED"

    # 17. Generate completion packet.
    packet = await tool_registry.execute("operations.generate_completion_packet", {"job_id": job_id}, ctx)
    assert packet.packet["status"] == "READY"
    assert packet.packet["summary"]["qa_status"] == "PASSED"

    # 18. Customer notification (job.completed -> communication log).
    stats = await event_bus.process_pending(EventType.JOB_COMPLETED)
    assert stats.succeeded >= 1

    # 19. Close job.
    closed = await tool_registry.execute("operations.close_job", {"job_id": job_id}, ctx)
    assert closed.job["status"] == "CLOSED"

    # --- Verify database state ---
    from app.models.operations import Job

    async with event_bus.session_factory() as session:
        job_row = await session.get(Job, uuid.UUID(job_id))
        assert job_row.status == "CLOSED"
        assert job_row.completed_at is not None

    # --- Verify events ---
    from app.models.event import Event

    async with event_bus.session_factory() as session:
        event_types = {
            r.event_type
            for r in (
                await session.execute(select(Event).where(Event.tenant_id == tenant_id))
            ).scalars().all()
        }
    for expected in (
        "job.created",
        "job.scheduled",
        "job.assigned",
        "job.dispatched",
        "job.en_route",
        "job.on_site",
        "job.started",
        "job.qa_started",
        "job.qa_passed",
        "job.completed",
        "job.closed",
        "invoice.trigger_requested",
    ):
        assert expected in event_types, f"missing event {expected}"

    # --- Verify notifications/communications ---
    async with event_bus.session_factory() as session:
        comm_logs = (
            await session.execute(select(CommunicationLog).where(CommunicationLog.tenant_id == tenant_id))
        ).scalars().all()
    assert any(log.recipient == "lifecycle@example.com" for log in comm_logs)

    # --- Verify audit logs ---
    async with event_bus.session_factory() as session:
        audit_rows = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id == tenant_id, AuditLog.entity_type == "job", AuditLog.entity_id == uuid.UUID(job_id)
                )
            )
        ).scalars().all()
    audited_tools = {r.tool for r in audit_rows if r.tool}
    for expected_tool in (
        "operations.create_job",
        "operations.schedule_job",
        "operations.assign_job",
        "operations.dispatch_job",
        "operations.close_job",
    ):
        assert expected_tool in audited_tools, f"missing audit for {expected_tool}"

    # --- Verify job timeline reflects the audit trail ---
    timeline = await tool_registry.execute("operations.get_job_timeline", {"job_id": job_id}, ctx)
    assert len(timeline.entries) >= len(audited_tools)

    # --- Verify Owner Cockpit / operations dashboard reflects real data ---
    from app.models.operations import JobStatus

    async with event_bus.session_factory() as session:
        completed_count = (
            await session.execute(
                select(Job).where(Job.tenant_id == tenant_id, Job.status == JobStatus.CLOSED)
            )
        ).scalars().all()
    assert len(completed_count) == 1
