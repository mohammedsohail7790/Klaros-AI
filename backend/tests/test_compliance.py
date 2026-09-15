"""Compliance tracking (licenses/insurance/certifications) — the "Licence
& liability" box from the One-Person Company diagram, previously missing
from the codebase entirely. Covers the tool-level behavior (create, list,
renew, the detect_expiring sweep creating real exceptions) plus the new
HTTP routes.
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


async def test_create_and_list_licenses(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    created = await tool_registry.execute(
        "compliance.create_license",
        {
            "type": "LIABILITY_INSURANCE",
            "name": "General Liability Policy",
            "expiry_date": str(date.today() + timedelta(days=200)),
        },
        _ctx(tenant_id),
    )
    assert created.license["status"] == "ACTIVE"

    listed = await tool_registry.execute("compliance.list_licenses", {}, _ctx(tenant_id))
    assert len(listed.licenses) == 1
    assert listed.licenses[0]["id"] == created.license["id"]


async def test_create_with_past_expiry_is_immediately_expired(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    created = await tool_registry.execute(
        "compliance.create_license",
        {"type": "PERMIT", "name": "Old Permit", "expiry_date": str(date.today() - timedelta(days=5))},
        _ctx(tenant_id),
    )
    assert created.license["status"] == "EXPIRED"


async def test_detect_expiring_flags_soon_and_expired(tool_registry, event_bus) -> None:
    tenant_id = uuid.uuid4()
    soon = await tool_registry.execute(
        "compliance.create_license",
        {"type": "CONTRACTOR_LICENSE", "name": "Expiring Soon License", "expiry_date": str(date.today() + timedelta(days=10))},
        _ctx(tenant_id),
    )
    far = await tool_registry.execute(
        "compliance.create_license",
        {"type": "BONDING", "name": "Far Out Bond", "expiry_date": str(date.today() + timedelta(days=200))},
        _ctx(tenant_id),
    )

    result = await tool_registry.execute("compliance.detect_expiring", {}, _ctx(tenant_id))
    assert result.newly_expiring_soon == [soon.license["id"]]
    assert result.newly_expired == []

    listed = await tool_registry.execute("compliance.list_licenses", {"status": "EXPIRING_SOON"}, _ctx(tenant_id))
    assert len(listed.licenses) == 1
    assert listed.licenses[0]["id"] == soon.license["id"]

    async def _count_license_exceptions() -> int:
        async with event_bus.session_factory() as session:
            rows = (
                await session.execute(
                    select(OperationsException).where(
                        OperationsException.tenant_id == tenant_id,
                        OperationsException.type == ExceptionType.LICENSE_EXPIRING_SOON,
                    )
                )
            ).scalars().all()
        return len(rows), rows

    count, rows = await _count_license_exceptions()
    assert count == 1
    assert rows[0].entity_id == uuid.UUID(soon.license["id"])

    # A repeat sweep must not re-flag or duplicate the exception.
    result2 = await tool_registry.execute("compliance.detect_expiring", {}, _ctx(tenant_id))
    assert result2.newly_expiring_soon == []
    count_after, _ = await _count_license_exceptions()
    assert count_after == 1


async def test_renew_clears_expiring_status(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    created = await tool_registry.execute(
        "compliance.create_license",
        {"type": "WORKERS_COMP_INSURANCE", "name": "Comp Policy", "expiry_date": str(date.today() + timedelta(days=5))},
        _ctx(tenant_id),
    )
    await tool_registry.execute("compliance.detect_expiring", {}, _ctx(tenant_id))
    listed = await tool_registry.execute("compliance.list_licenses", {}, _ctx(tenant_id))
    assert listed.licenses[0]["status"] == "EXPIRING_SOON"

    renewed = await tool_registry.execute(
        "compliance.renew_license",
        {"license_id": created.license["id"], "expiry_date": str(date.today() + timedelta(days=365))},
        _ctx(tenant_id),
    )
    assert renewed.license["status"] == "ACTIVE"
    assert renewed.license["expiry_date"] == str(date.today() + timedelta(days=365))


async def test_licenses_isolated_per_tenant(tool_registry) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await tool_registry.execute(
        "compliance.create_license",
        {"type": "OTHER", "name": "Tenant A Doc", "expiry_date": str(date.today() + timedelta(days=100))},
        _ctx(tenant_a),
    )
    listed_b = await tool_registry.execute("compliance.list_licenses", {}, _ctx(tenant_b))
    assert listed_b.licenses == []


async def test_compliance_over_http(client) -> None:
    register = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "HTTP Compliance Co", "full_name": "Owner Test",
            "email": "owner@httpcomplianceco.com", "password": "supersecret1",
        },
    )
    assert register.status_code == 201
    token = register.json()["tokens"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/api/v1/compliance/licenses",
        json={
            "type": "BUSINESS_LICENSE",
            "name": "City Business License",
            "expiry_date": str(date.today() + timedelta(days=90)),
        },
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    license_id = create_resp.json()["license"]["id"]

    list_resp = await client.get("/api/v1/compliance/licenses", headers=headers)
    assert list_resp.status_code == 200
    assert len(list_resp.json()["licenses"]) == 1

    detect_resp = await client.post("/api/v1/compliance/detect-expiring", headers=headers)
    assert detect_resp.status_code == 200, detect_resp.text

    renew_resp = await client.post(
        f"/api/v1/compliance/licenses/{license_id}/renew",
        json={"expiry_date": str(date.today() + timedelta(days=365))},
        headers=headers,
    )
    assert renew_resp.status_code == 200, renew_resp.text
    assert renew_resp.json()["license"]["status"] == "ACTIVE"
