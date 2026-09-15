"""Vendor/subcontractor management — the Vendor/VendorBill/Payout models
and finance.* tools already existed and were registered in the
ToolRegistry, but no route ever exposed them (the whole feature was
unreachable). Covers the real tool-level behavior (list tools that were
missing entirely, the SUBCONTRACTOR JobCost side-effect on record_bill,
payout requiring approval by default policy) plus the new HTTP routes."""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.models.actor import ActorType
from app.models.finance import JobCost
from app.models.operations import Job, JobPriority, JobStatus
from app.models.crm import Customer
from app.models.rbac import Role
from app.tools.base import ExecutionContext
from app.tools.errors import ToolApprovalRequiredError

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id, role=Role.OWNER):
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


async def _make_job(event_bus, tenant_id) -> Job:
    async with event_bus.session_factory() as session:
        customer = Customer(tenant_id=tenant_id, name="Vendor Test Customer")
        session.add(customer)
        await session.flush()
        job = Job(
            tenant_id=tenant_id, customer_id=customer.id, job_number=f"JOB-{uuid.uuid4().hex[:8]}",
            title="Vendor Test Job", status=JobStatus.SCHEDULED, priority=JobPriority.NORMAL,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job


async def test_create_and_list_vendors(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    created = await tool_registry.execute(
        "finance.create_vendor", {"name": "Acme Subcontracting", "email": "acme@example.com"}, _ctx(tenant_id)
    )
    assert created.vendor["name"] == "Acme Subcontracting"

    listed = await tool_registry.execute("finance.list_vendors", {}, _ctx(tenant_id))
    assert len(listed.vendors) == 1
    assert listed.vendors[0]["id"] == created.vendor["id"]


async def test_record_bill_without_job_creates_no_job_cost(tool_registry, event_bus) -> None:
    tenant_id = uuid.uuid4()
    vendor = await tool_registry.execute("finance.create_vendor", {"name": "No Job Vendor"}, _ctx(tenant_id))

    bill = await tool_registry.execute(
        "finance.record_vendor_bill",
        {"vendor_id": vendor.vendor["id"], "amount": "500.00", "due_date": "2026-10-01"},
        _ctx(tenant_id),
    )
    assert bill.vendor_bill["amount"] == "500.00"
    assert bill.vendor_bill["job_id"] is None


async def test_record_bill_with_job_creates_subcontractor_job_cost(tool_registry, event_bus) -> None:
    tenant_id = uuid.uuid4()
    job = await _make_job(event_bus, tenant_id)
    vendor = await tool_registry.execute("finance.create_vendor", {"name": "Job Vendor"}, _ctx(tenant_id))

    bill = await tool_registry.execute(
        "finance.record_vendor_bill",
        {"vendor_id": vendor.vendor["id"], "job_id": str(job.id), "amount": "750.00", "due_date": "2026-10-01"},
        _ctx(tenant_id),
    )
    assert bill.vendor_bill["job_id"] == str(job.id)

    async with event_bus.session_factory() as session:
        from sqlalchemy import select

        costs = (
            await session.execute(select(JobCost).where(JobCost.tenant_id == tenant_id, JobCost.job_id == job.id))
        ).scalars().all()
    assert len(costs) == 1
    assert costs[0].category == "SUBCONTRACTOR"
    assert costs[0].unit_cost == Decimal("750.00")


async def test_list_vendor_bills_filters_by_vendor(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    v1 = await tool_registry.execute("finance.create_vendor", {"name": "Vendor One"}, _ctx(tenant_id))
    v2 = await tool_registry.execute("finance.create_vendor", {"name": "Vendor Two"}, _ctx(tenant_id))
    await tool_registry.execute(
        "finance.record_vendor_bill",
        {"vendor_id": v1.vendor["id"], "amount": "100.00", "due_date": "2026-10-01"}, _ctx(tenant_id),
    )
    await tool_registry.execute(
        "finance.record_vendor_bill",
        {"vendor_id": v2.vendor["id"], "amount": "200.00", "due_date": "2026-10-01"}, _ctx(tenant_id),
    )

    all_bills = await tool_registry.execute("finance.list_vendor_bills", {}, _ctx(tenant_id))
    assert len(all_bills.vendor_bills) == 2

    v1_bills = await tool_registry.execute("finance.list_vendor_bills", {"vendor_id": v1.vendor["id"]}, _ctx(tenant_id))
    assert len(v1_bills.vendor_bills) == 1
    assert v1_bills.vendor_bills[0]["amount"] == "100.00"


async def test_record_payout_requires_approval_by_default_policy(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    vendor = await tool_registry.execute("finance.create_vendor", {"name": "Payout Vendor"}, _ctx(tenant_id))
    bill = await tool_registry.execute(
        "finance.record_vendor_bill",
        {"vendor_id": vendor.vendor["id"], "amount": "300.00", "due_date": "2026-10-01"}, _ctx(tenant_id),
    )
    with pytest.raises(ToolApprovalRequiredError):
        await tool_registry.execute(
            "finance.record_payout",
            {"vendor_id": vendor.vendor["id"], "bill_id": bill.vendor_bill["id"]},
            _ctx(tenant_id),
        )


async def test_vendors_isolated_per_tenant(tool_registry) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await tool_registry.execute("finance.create_vendor", {"name": "Tenant A Vendor"}, _ctx(tenant_a))

    listed_b = await tool_registry.execute("finance.list_vendors", {}, _ctx(tenant_b))
    assert listed_b.vendors == []


async def test_vendors_over_http(client) -> None:
    register = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "HTTP Vendor Co", "full_name": "Owner Test",
            "email": "owner@httpvendorco.com", "password": "supersecret1",
        },
    )
    assert register.status_code == 201
    token = register.json()["tokens"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/api/v1/vendors", json={"name": "HTTP Test Vendor"}, headers=headers
    )
    assert create_resp.status_code == 201, create_resp.text
    vendor_id = create_resp.json()["vendor"]["id"]

    list_resp = await client.get("/api/v1/vendors", headers=headers)
    assert list_resp.status_code == 200
    assert len(list_resp.json()["vendors"]) == 1

    bill_resp = await client.post(
        "/api/v1/vendors/bills",
        json={"vendor_id": vendor_id, "amount": "400.00", "due_date": "2026-10-01"},
        headers=headers,
    )
    assert bill_resp.status_code == 201, bill_resp.text
    bill_id = bill_resp.json()["vendor_bill"]["id"]

    bills_resp = await client.get("/api/v1/vendors/bills", headers=headers)
    assert bills_resp.status_code == 200
    assert len(bills_resp.json()["vendor_bills"]) == 1

    payout_resp = await client.post(
        f"/api/v1/vendors/bills/{bill_id}/payout", json={"vendor_id": vendor_id, "bill_id": bill_id}, headers=headers
    )
    assert payout_resp.status_code == 202  # ToolApprovalRequiredError -> pending_approval
