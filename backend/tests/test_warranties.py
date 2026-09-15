"""Warranty tracking — the "warranty check-in" box from the One-Person
Company diagram's Retention & Referral panel, previously missing from
the codebase entirely. Covers tool-level behavior (create, list,
check-in, detect_expiring flagging + exception creation) plus routes.
"""

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models.actor import ActorType
from app.models.operations import ExceptionType, OperationsException
from app.models.rbac import Role
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id, role=Role.OWNER):
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


async def test_create_and_list_warranties(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    customer_id = uuid.uuid4()
    created = await tool_registry.execute(
        "retention.create_warranty",
        {
            "customer_id": str(customer_id),
            "item_description": "HVAC unit",
            "start_date": str(date.today()),
            "expiry_date": str(date.today() + timedelta(days=200)),
        },
        _ctx(tenant_id),
    )
    assert created.warranty["status"] == "ACTIVE"

    listed = await tool_registry.execute("retention.list_warranties", {}, _ctx(tenant_id))
    assert len(listed.warranties) == 1
    assert listed.warranties[0]["id"] == created.warranty["id"]


async def test_create_with_past_expiry_is_immediately_expired(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    created = await tool_registry.execute(
        "retention.create_warranty",
        {
            "customer_id": str(uuid.uuid4()),
            "item_description": "Old Unit",
            "start_date": str(date.today() - timedelta(days=400)),
            "expiry_date": str(date.today() - timedelta(days=5)),
        },
        _ctx(tenant_id),
    )
    assert created.warranty["status"] == "EXPIRED"


async def test_check_in_records_timestamp_and_notes(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    created = await tool_registry.execute(
        "retention.create_warranty",
        {
            "customer_id": str(uuid.uuid4()),
            "item_description": "Water Heater",
            "start_date": str(date.today()),
            "expiry_date": str(date.today() + timedelta(days=200)),
        },
        _ctx(tenant_id),
    )
    checked = await tool_registry.execute(
        "retention.check_in_warranty",
        {"warranty_id": created.warranty["id"], "notes": "Called customer, all good."},
        _ctx(tenant_id),
    )
    assert checked.warranty["last_checked_in_at"] is not None
    assert checked.warranty["notes"] == "Called customer, all good."


async def test_detect_expiring_flags_and_creates_exception(tool_registry, event_bus) -> None:
    tenant_id = uuid.uuid4()
    soon = await tool_registry.execute(
        "retention.create_warranty",
        {
            "customer_id": str(uuid.uuid4()),
            "item_description": "Expiring Soon Unit",
            "start_date": str(date.today() - timedelta(days=300)),
            "expiry_date": str(date.today() + timedelta(days=10)),
        },
        _ctx(tenant_id),
    )
    await tool_registry.execute(
        "retention.create_warranty",
        {
            "customer_id": str(uuid.uuid4()),
            "item_description": "Far Out Unit",
            "start_date": str(date.today()),
            "expiry_date": str(date.today() + timedelta(days=200)),
        },
        _ctx(tenant_id),
    )

    result = await tool_registry.execute("retention.detect_expiring_warranties", {}, _ctx(tenant_id))
    assert result.newly_expiring_soon == [soon.warranty["id"]]
    assert result.newly_expired == []

    async with event_bus.session_factory() as session:
        rows = (
            await session.execute(
                select(OperationsException).where(
                    OperationsException.tenant_id == tenant_id,
                    OperationsException.type == ExceptionType.WARRANTY_EXPIRING_SOON,
                )
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].entity_id == uuid.UUID(soon.warranty["id"])

    # A repeat sweep must not duplicate the exception.
    result2 = await tool_registry.execute("retention.detect_expiring_warranties", {}, _ctx(tenant_id))
    assert result2.newly_expiring_soon == []


async def test_warranties_isolated_per_tenant(tool_registry) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await tool_registry.execute(
        "retention.create_warranty",
        {
            "customer_id": str(uuid.uuid4()),
            "item_description": "Tenant A Unit",
            "start_date": str(date.today()),
            "expiry_date": str(date.today() + timedelta(days=100)),
        },
        _ctx(tenant_a),
    )
    listed_b = await tool_registry.execute("retention.list_warranties", {}, _ctx(tenant_b))
    assert listed_b.warranties == []


async def test_warranties_over_http(client) -> None:
    register = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "HTTP Warranty Co", "full_name": "Owner Test",
            "email": "owner@httpwarrantyco.com", "password": "supersecret1",
        },
    )
    assert register.status_code == 201
    token = register.json()["tokens"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/api/v1/warranties",
        json={
            "customer_id": str(uuid.uuid4()),
            "item_description": "HTTP Test Unit",
            "start_date": str(date.today()),
            "expiry_date": str(date.today() + timedelta(days=90)),
        },
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    warranty_id = create_resp.json()["warranty"]["id"]

    list_resp = await client.get("/api/v1/warranties", headers=headers)
    assert list_resp.status_code == 200
    assert len(list_resp.json()["warranties"]) == 1

    check_in_resp = await client.post(
        f"/api/v1/warranties/{warranty_id}/check-in", json={"notes": "Checked in."}, headers=headers
    )
    assert check_in_resp.status_code == 200, check_in_resp.text
    assert check_in_resp.json()["warranty"]["last_checked_in_at"] is not None

    detect_resp = await client.post("/api/v1/warranties/detect-expiring", headers=headers)
    assert detect_resp.status_code == 200, detect_resp.text
