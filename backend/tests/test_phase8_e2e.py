"""Phase 8 critical E2E: a business event is published once and then
automatically propagates through Klaros — retention lifecycle, finance,
service recovery, and the Morning Brief all update themselves via a real,
continuously-running EventWorker background task. This test never calls
`bus.process_pending` or `POST /api/v1/events/process/{event_type}`
directly — that would defeat the entire point of Phase 8.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.events.bus import EventBus
from app.events.metrics import EventWorkerMetrics
from app.events.worker import EventWorker
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.finance import Invoice
from app.models.operations import ExceptionStatus, OperationsException
from app.models.rbac import Role
from app.models.retention import CustomerLifecycleProfile, LifecycleState
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id, role=Role.OWNER):
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


async def test_full_loop_processes_automatically_without_manual_event_processing(
    event_bus: EventBus, tool_registry
) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    # This test's tool calls and the worker's ticks are genuinely concurrent
    # asyncio tasks sharing the *same* SQLite StaticPool connection (see
    # app/db/session.py) — this test suite's substitute for Postgres. A
    # shared asyncio.Lock here only serializes access to that one physical
    # test connection; it plays no role in — and is not a substitute for —
    # the real concurrency-safety guarantee, which is the unique
    # (event_id, handler_name) row together with Redis Streams consumer-group
    # delivery in production (see app/events/worker.py's docstring and
    # tests/test_event_worker.py's dedicated concurrency tests). The worker
    # task below is still a real, independently-scheduled background loop —
    # nothing in this test calls `process_pending` or the manual endpoint.
    db_lock = asyncio.Lock()

    async def call(tool_name: str, tool_input: dict):
        async with db_lock:
            return await tool_registry.execute(tool_name, tool_input, ctx)

    async def read(fn):
        async with db_lock:
            async with event_bus.session_factory() as session:
                return await fn(session)

    async def wait_for(predicate, *, timeout=5.0, interval=0.03):
        elapsed = 0.0
        while elapsed < timeout:
            if await read(predicate):
                return True
            await asyncio.sleep(interval)
            elapsed += interval
        return False

    # 1. Start the real continuous worker BEFORE any business action happens —
    # everything from here on must be picked up by its own poll loop.
    worker = EventWorker(event_bus, poll_interval_seconds=0.02, metrics=EventWorkerMetrics(), db_lock=db_lock)
    shutdown = asyncio.Event()
    worker_task = asyncio.create_task(worker.run_forever(shutdown))

    try:
        # 2. Create tenant (implicit — tenant_id is the isolation boundary
        # here, no separate Organization row is required for tool calls).
        # 3. Create customer.
        customer = await call("crm.create_customer", {"name": "Auto Loop Co"})
        customer_id = customer.customer["id"]

        # 4. Create job.
        job = await call(
            "operations.create_job",
            {"title": "Full loop repair", "customer_id": customer_id, "estimated_revenue": 500.0},
        )
        job_id = job.job["id"]
        await call(
            "operations.schedule_job",
            {"job_id": job_id, "start_time": "2026-11-01T09:00:00+00:00", "end_time": "2026-11-01T11:00:00+00:00"},
        )
        worker_row = await call("operations.create_worker", {"name": "Tech", "service_types": ["HVAC"]})
        await call("operations.assign_job", {"job_id": job_id, "worker_id": worker_row.worker["id"]})
        await call("operations.dispatch_job", {"job_id": job_id})
        await call("operations.update_job_status", {"job_id": job_id, "target_status": "EN_ROUTE"})
        await call("operations.update_job_status", {"job_id": job_id, "target_status": "ON_SITE"})
        await call("operations.start_job", {"job_id": job_id})
        await call(
            "operations.add_job_photo",
            {"job_id": job_id, "filename": "done.jpg", "content_base64": "AAAA", "content_type": "image/jpeg"},
        )
        await call("operations.complete_job", {"job_id": job_id})
        await call("operations.start_qa", {"job_id": job_id})
        await call("operations.complete_qa", {"job_id": job_id})
        await call("operations.generate_completion_packet", {"job_id": job_id})

        # 5. Close job — this is the event that must propagate automatically.
        await call("operations.close_job", {"job_id": job_id})

        # 6. Worker automatically processes job.closed -> retention lifecycle
        # updates WITHOUT this test calling process_pending.
        async def lifecycle_is_first_service(session) -> bool:
            profile = (
                await session.execute(
                    select(CustomerLifecycleProfile).where(
                        CustomerLifecycleProfile.tenant_id == tenant_id,
                        CustomerLifecycleProfile.customer_id == uuid.UUID(customer_id),
                    )
                )
            ).scalar_one_or_none()
            return profile is not None and profile.lifecycle_state == LifecycleState.FIRST_SERVICE

        assert await wait_for(lifecycle_is_first_service), "retention lifecycle never auto-updated"

        # 7. Invoice is created (auto-triggered by job.closed's own handler chain).
        async def invoice_exists(session) -> bool:
            row = (
                await session.execute(
                    select(Invoice).where(Invoice.tenant_id == tenant_id, Invoice.job_id == uuid.UUID(job_id))
                )
            ).scalar_one_or_none()
            return row is not None

        assert await wait_for(invoice_exists), "invoice was never auto-created from job.closed"

        invoice_row = await read(
            lambda session: session.execute(
                select(Invoice).where(Invoice.tenant_id == tenant_id, Invoice.job_id == uuid.UUID(job_id))
            )
        )
        invoice_id = str(invoice_row.scalar_one().id)

        await call("finance.request_invoice_approval", {"invoice_id": invoice_id})
        await call("finance.send_invoice", {"invoice_id": invoice_id})

        # 8. Payment is received.
        await call(
            "finance.record_test_payment",
            {
                "customer_id": customer_id,
                "amount": 500.0,
                "allocations": [{"invoice_id": invoice_id, "amount": 500.0}],
            },
        )

        # 9. Worker processes payment.received automatically -> invoice PAID.
        async def invoice_is_paid(session) -> bool:
            row = await session.get(Invoice, uuid.UUID(invoice_id))
            return row.status == "PAID"

        assert await wait_for(invoice_is_paid), "invoice never auto-transitioned to PAID"

        # 11. Negative feedback.
        await call(
            "retention.record_feedback",
            {"customer_id": customer_id, "rating": 1, "comment": "Worker broke a fixture"},
        )

        # 13. Service recovery exception appears automatically.
        async def service_recovery_exception_exists(session) -> bool:
            rows = (
                await session.execute(
                    select(OperationsException).where(
                        OperationsException.tenant_id == tenant_id,
                        OperationsException.type == "SERVICE_RECOVERY_REQUIRED",
                        OperationsException.status == ExceptionStatus.OPEN,
                    )
                )
            ).scalars().all()
            return len(rows) >= 1

        assert await wait_for(service_recovery_exception_exists), "service recovery exception never auto-appeared"

        # 14. Generate Morning Brief.
        await call("insights.generate_morning_brief", {})
        latest = await call("insights.get_latest_morning_brief", {})

        # 15. Morning Brief reports the real exception.
        assert any("service recovery" in i.summary.lower() for i in latest.insights if i.category == "RETENTION")
        # 16. Morning Brief reports real revenue.
        assert any("500" in i.summary for i in latest.insights if i.category == "FINANCE")
        # 17. Morning Brief reports real customer activity (the stalled/negative-feedback recommendation).
        assert any(r.related_entity_id == customer_id for r in latest.recommendations)

        # 19. Verify no duplicate effects: re-processing more ticks must not
        # create a second lifecycle-advance, a second invoice, or a second
        # exception (idempotency holds under a live, still-running worker).
        await asyncio.sleep(0.1)

        async def count_invoices_and_exceptions(session):
            invoices = (
                await session.execute(
                    select(Invoice).where(Invoice.tenant_id == tenant_id, Invoice.job_id == uuid.UUID(job_id))
                )
            ).scalars().all()
            exceptions = (
                await session.execute(
                    select(OperationsException).where(
                        OperationsException.tenant_id == tenant_id,
                        OperationsException.type == "SERVICE_RECOVERY_REQUIRED",
                    )
                )
            ).scalars().all()
            return len(invoices), len(exceptions)

        invoice_count, exception_count = await read(count_invoices_and_exceptions)
        assert invoice_count == 1
        assert exception_count == 1

        # 18. Verify audit logs exist for the AI-driven insight calls and the
        # human-driven mutations, both real rows in the same audit table.
        async def audit_counts(session):
            ai_rows = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.tenant_id == tenant_id,
                        AuditLog.actor_type == ActorType.AI,
                        AuditLog.tool == "insights.get_retention_snapshot",
                    )
                )
            ).scalars().all()
            user_rows = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.tenant_id == tenant_id,
                        AuditLog.actor_type == ActorType.USER,
                        AuditLog.tool == "operations.close_job",
                    )
                )
            ).scalars().all()
            return len(ai_rows), len(user_rows)

        ai_count, user_count = await read(audit_counts)
        assert ai_count >= 1
        assert user_count >= 1

    finally:
        shutdown.set()
        await worker_task
