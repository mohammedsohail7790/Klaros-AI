import uuid

import pytest

from app.models.actor import ActorType
from app.models.rbac import Role
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id, role=Role.OWNER):
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


async def _make_customer(tool_registry, tenant_id) -> str:
    out = await tool_registry.execute("crm.create_customer", {"name": "Test Customer"}, _ctx(tenant_id))
    return out.customer["id"]


async def test_create_job_directly(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    customer_id = await _make_customer(tool_registry, tenant_id)

    out = await tool_registry.execute(
        "operations.create_job",
        {"title": "Fix furnace", "customer_id": customer_id, "service_type": "HVAC"},
        _ctx(tenant_id),
    )
    assert out.job["status"] == "DRAFT"
    assert out.job["job_number"].startswith("JOB-")
    assert out.deduplicated is False


async def test_create_job_from_appointment_is_idempotent(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    customer_id = await _make_customer(tool_registry, tenant_id)

    appt = await tool_registry.execute(
        "crm.create_appointment",
        {
            "customer_id": customer_id,
            "title": "AC repair",
            "start_time": "2026-10-01T09:00:00+00:00",
            "end_time": "2026-10-01T10:00:00+00:00",
        },
        _ctx(tenant_id),
    )
    appointment_id = appt.appointment["id"]

    first = await tool_registry.execute(
        "operations.create_job", {"title": "AC repair", "customer_id": customer_id, "appointment_id": appointment_id}, _ctx(tenant_id)
    )
    second = await tool_registry.execute(
        "operations.create_job", {"title": "AC repair", "customer_id": customer_id, "appointment_id": appointment_id}, _ctx(tenant_id)
    )

    assert first.job["id"] == second.job["id"]
    assert first.deduplicated is False
    assert second.deduplicated is True


async def test_update_job(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    customer_id = await _make_customer(tool_registry, tenant_id)
    created = await tool_registry.execute(
        "operations.create_job", {"title": "Job A", "customer_id": customer_id}, _ctx(tenant_id)
    )
    updated = await tool_registry.execute(
        "operations.update_job",
        {"job_id": created.job["id"], "priority": "URGENT", "internal_notes": "call ahead"},
        _ctx(tenant_id),
    )
    assert updated.job["priority"] == "URGENT"
    assert updated.job["internal_notes"] == "call ahead"


async def test_update_job_rejects_unknown_priority(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    customer_id = await _make_customer(tool_registry, tenant_id)
    created = await tool_registry.execute(
        "operations.create_job", {"title": "Job A", "customer_id": customer_id}, _ctx(tenant_id)
    )

    with pytest.raises(ValueError, match="Invalid job priority"):
        await tool_registry.execute(
            "operations.update_job",
            {"job_id": created.job["id"], "priority": "NOT_A_REAL_PRIORITY"},
            _ctx(tenant_id),
        )


async def test_search_jobs_filters_and_paginates(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    customer_id = await _make_customer(tool_registry, tenant_id)
    for i in range(3):
        await tool_registry.execute(
            "operations.create_job", {"title": f"Job {i}", "customer_id": customer_id}, _ctx(tenant_id)
        )
    result = await tool_registry.execute("operations.search_jobs", {"limit": 2}, _ctx(tenant_id))
    assert result.total == 3
    assert len(result.jobs) == 2
