import uuid

import pytest

from app.models.actor import ActorType
from app.models.rbac import Role
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id, role=Role.OWNER):
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


async def _customer(tool_registry, tenant_id) -> str:
    out = await tool_registry.execute("crm.create_customer", {"name": "C"}, _ctx(tenant_id))
    return out.customer["id"]


async def _job(tool_registry, tenant_id, customer_id, **overrides) -> dict:
    payload = {"title": "Job", "customer_id": customer_id, **overrides}
    out = await tool_registry.execute("operations.create_job", payload, _ctx(tenant_id))
    return out.job


async def _worker(tool_registry, tenant_id, **overrides) -> str:
    payload = {"name": "Ahmed", **overrides}
    out = await tool_registry.execute("operations.create_worker", payload, _ctx(tenant_id))
    return out.worker["id"]


async def test_schedule_moves_draft_to_scheduled(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    customer_id = await _customer(tool_registry, tenant_id)
    job = await _job(tool_registry, tenant_id, customer_id)

    scheduled = await tool_registry.execute(
        "operations.schedule_job",
        {"job_id": job["id"], "start_time": "2026-10-05T09:00:00+00:00", "end_time": "2026-10-05T10:00:00+00:00"},
        _ctx(tenant_id),
    )
    assert scheduled.job["status"] == "SCHEDULED"


async def test_assign_job_to_inactive_worker_rejected(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    customer_id = await _customer(tool_registry, tenant_id)
    job = await _job(tool_registry, tenant_id, customer_id)
    worker_id = await _worker(tool_registry, tenant_id)

    await tool_registry.execute(
        "operations.update_worker_status", {"worker_id": worker_id, "status": "INACTIVE"}, _ctx(tenant_id)
    )

    with pytest.raises(ValueError, match="not available"):
        await tool_registry.execute("operations.assign_job", {"job_id": job["id"], "worker_id": worker_id}, _ctx(tenant_id))


async def test_update_worker_status_rejects_unknown_status(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    worker_id = await _worker(tool_registry, tenant_id)

    with pytest.raises(ValueError, match="Invalid worker status"):
        await tool_registry.execute(
            "operations.update_worker_status", {"worker_id": worker_id, "status": "NOT_A_REAL_STATUS"}, _ctx(tenant_id)
        )


async def test_assign_job_schedule_conflict_rejected(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    customer_id = await _customer(tool_registry, tenant_id)
    worker_id = await _worker(tool_registry, tenant_id)

    job_a = await _job(tool_registry, tenant_id, customer_id)
    await tool_registry.execute(
        "operations.schedule_job",
        {"job_id": job_a["id"], "start_time": "2026-10-06T09:00:00+00:00", "end_time": "2026-10-06T10:00:00+00:00"},
        _ctx(tenant_id),
    )
    await tool_registry.execute("operations.assign_job", {"job_id": job_a["id"], "worker_id": worker_id}, _ctx(tenant_id))

    job_b = await _job(tool_registry, tenant_id, customer_id)
    await tool_registry.execute(
        "operations.schedule_job",
        {"job_id": job_b["id"], "start_time": "2026-10-06T09:30:00+00:00", "end_time": "2026-10-06T10:30:00+00:00"},
        _ctx(tenant_id),
    )

    with pytest.raises(ValueError, match="overlapping"):
        await tool_registry.execute("operations.assign_job", {"job_id": job_b["id"], "worker_id": worker_id}, _ctx(tenant_id))


async def test_assign_job_wrong_tenant_worker_rejected(tool_registry) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    customer_id = await _customer(tool_registry, tenant_a)
    job = await _job(tool_registry, tenant_a, customer_id)
    worker_b = await _worker(tool_registry, tenant_b)

    with pytest.raises(ValueError, match="not found"):
        await tool_registry.execute("operations.assign_job", {"job_id": job["id"], "worker_id": worker_b}, _ctx(tenant_a))


async def test_unassign_job(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    customer_id = await _customer(tool_registry, tenant_id)
    job = await _job(tool_registry, tenant_id, customer_id)
    worker_id = await _worker(tool_registry, tenant_id)
    await tool_registry.execute("operations.assign_job", {"job_id": job["id"], "worker_id": worker_id}, _ctx(tenant_id))

    result = await tool_registry.execute("operations.unassign_job", {"job_id": job["id"]}, _ctx(tenant_id))
    assert result.job["assigned_user_id"] is None


async def test_full_dispatch_chain(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    customer_id = await _customer(tool_registry, tenant_id)
    job = await _job(tool_registry, tenant_id, customer_id)

    await tool_registry.execute(
        "operations.schedule_job",
        {"job_id": job["id"], "start_time": "2026-10-07T09:00:00+00:00", "end_time": "2026-10-07T10:00:00+00:00"},
        _ctx(tenant_id),
    )
    dispatched = await tool_registry.execute("operations.dispatch_job", {"job_id": job["id"]}, _ctx(tenant_id))
    assert dispatched.job["status"] == "DISPATCHED"

    en_route = await tool_registry.execute(
        "operations.update_job_status", {"job_id": job["id"], "target_status": "EN_ROUTE"}, _ctx(tenant_id)
    )
    assert en_route.job["status"] == "EN_ROUTE"

    on_site = await tool_registry.execute(
        "operations.update_job_status", {"job_id": job["id"], "target_status": "ON_SITE"}, _ctx(tenant_id)
    )
    assert on_site.job["status"] == "ON_SITE"

    started = await tool_registry.execute("operations.start_job", {"job_id": job["id"]}, _ctx(tenant_id))
    assert started.job["status"] == "IN_PROGRESS"
    assert started.job["actual_start"] is not None


async def test_reject_dispatch_before_scheduled(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    customer_id = await _customer(tool_registry, tenant_id)
    job = await _job(tool_registry, tenant_id, customer_id)

    with pytest.raises(ValueError, match="Cannot transition"):
        await tool_registry.execute("operations.dispatch_job", {"job_id": job["id"]}, _ctx(tenant_id))


async def test_block_and_unblock_job_resolves_exception(tool_registry) -> None:
    from sqlalchemy import select

    from app.models.operations import ExceptionStatus, OperationsException

    tenant_id = uuid.uuid4()
    customer_id = await _customer(tool_registry, tenant_id)
    job = await _job(tool_registry, tenant_id, customer_id)
    await tool_registry.execute(
        "operations.schedule_job",
        {"job_id": job["id"], "start_time": "2026-10-08T09:00:00+00:00", "end_time": "2026-10-08T10:00:00+00:00"},
        _ctx(tenant_id),
    )
    await tool_registry.execute("operations.dispatch_job", {"job_id": job["id"]}, _ctx(tenant_id))

    blocked = await tool_registry.execute(
        "operations.block_job", {"job_id": job["id"], "reason": "no access to unit"}, _ctx(tenant_id)
    )
    assert blocked.job["status"] == "BLOCKED"

    unblocked = await tool_registry.execute(
        "operations.unblock_job", {"job_id": job["id"], "target_status": "DISPATCHED"}, _ctx(tenant_id)
    )
    assert unblocked.job["status"] == "DISPATCHED"

    async with tool_registry._session_factory() as session:
        rows = (
            await session.execute(
                select(OperationsException).where(
                    OperationsException.entity_id == uuid.UUID(job["id"]),
                    OperationsException.status == ExceptionStatus.RESOLVED,
                )
            )
        ).scalars().all()
    assert len(rows) == 1
