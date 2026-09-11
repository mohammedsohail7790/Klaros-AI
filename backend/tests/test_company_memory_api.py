"""app/api/v1/company_memory.py — the authenticated REST API, exercised
over real HTTP (httpx ASGITransport), matching every other domain's own
*_api.py test convention (see tests/test_crm_api.py)."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def _register(client: AsyncClient, org: str, email: str) -> str:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"organization_name": org, "full_name": "Owner", "email": email, "password": "supersecret1"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["tokens"]["access_token"]


async def test_create_list_and_context_over_http(client: AsyncClient) -> None:
    token = await _register(client, "Memory API Co", "owner@memoryapi.com")
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/api/v1/memory",
        json={"memory_type": "OWNER_PREFERENCE", "key": "preferred_appointment_time", "value": "morning"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "ACTIVE"
    memory_id = created.json()["id"]

    listing = await client.get("/api/v1/memory", headers=headers)
    assert listing.status_code == 200
    assert len(listing.json()["memories"]) == 1

    context = await client.get("/api/v1/memory/context", headers=headers)
    assert context.status_code == 200
    assert context.json()["context"] == [
        {"memory_type": "OWNER_PREFERENCE", "key": "preferred_appointment_time", "value": "morning", "source": "OWNER_EXPLICIT"}
    ]

    detail = await client.get(f"/api/v1/memory/{memory_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["value"] == "morning"


async def test_supersession_and_history_over_http(client: AsyncClient) -> None:
    token = await _register(client, "Memory History Co", "owner@memoryhistory.com")
    headers = {"Authorization": f"Bearer {token}"}

    await client.post(
        "/api/v1/memory",
        json={"memory_type": "OWNER_PREFERENCE", "key": "preferred_appointment_time", "value": "morning"},
        headers=headers,
    )
    await client.post(
        "/api/v1/memory",
        json={"memory_type": "OWNER_PREFERENCE", "key": "preferred_appointment_time", "value": "afternoon"},
        headers=headers,
    )

    history = await client.get("/api/v1/memory/history/preferred_appointment_time", headers=headers)
    assert history.status_code == 200
    values = [h["value"] for h in history.json()["history"]]
    assert values == ["morning", "afternoon"]
    statuses = [h["status"] for h in history.json()["history"]]
    assert statuses == ["ARCHIVED", "ACTIVE"]

    context = await client.get("/api/v1/memory/context", headers=headers)
    assert len(context.json()["context"]) == 1
    assert context.json()["context"][0]["value"] == "afternoon"


async def test_revoke_removes_from_context_over_http(client: AsyncClient) -> None:
    token = await _register(client, "Memory Revoke Co", "owner@memoryrevoke.com")
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/api/v1/memory",
        json={"memory_type": "OWNER_PREFERENCE", "key": "preferred_appointment_time", "value": "morning"},
        headers=headers,
    )
    memory_id = created.json()["id"]

    revoked = await client.post(f"/api/v1/memory/{memory_id}/revoke", json={"reason": "no longer applies"}, headers=headers)
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "REVOKED"

    context = await client.get("/api/v1/memory/context", headers=headers)
    assert context.json()["context"] == []


async def test_invalid_key_rejected_with_422_over_http(client: AsyncClient) -> None:
    token = await _register(client, "Memory Validation Co", "owner@memoryvalidation.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/memory",
        json={"memory_type": "OWNER_PREFERENCE", "key": "Not A Valid Key!", "value": "morning"},
        headers=headers,
    )
    assert resp.status_code == 422


async def test_authority_conflict_rejected_with_422_over_http(client: AsyncClient) -> None:
    token = await _register(client, "Memory Authority Co", "owner@memoryauthority.com")
    headers = {"Authorization": f"Bearer {token}"}

    await client.post(
        "/api/v1/memory",
        json={"memory_type": "OWNER_PREFERENCE", "key": "preferred_appointment_time", "value": "morning", "source": "OWNER_EXPLICIT"},
        headers=headers,
    )
    resp = await client.post(
        "/api/v1/memory",
        json={"memory_type": "OWNER_PREFERENCE", "key": "preferred_appointment_time", "value": "afternoon", "source": "OWNER_APPROVAL"},
        headers=headers,
    )
    assert resp.status_code == 422


async def test_memory_is_tenant_isolated_over_http(client: AsyncClient) -> None:
    token_a = await _register(client, "Memory Tenant A", "owner@memorytenanta.com")
    token_b = await _register(client, "Memory Tenant B", "owner@memorytenantb.com")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    created = await client.post(
        "/api/v1/memory",
        json={"memory_type": "OWNER_PREFERENCE", "key": "preferred_appointment_time", "value": "morning"},
        headers=headers_a,
    )
    memory_id = created.json()["id"]

    # Tenant B cannot read tenant A's memory.
    get_b = await client.get(f"/api/v1/memory/{memory_id}", headers=headers_b)
    assert get_b.status_code == 404

    # Tenant B cannot revoke tenant A's memory (IDOR check).
    revoke_b = await client.post(f"/api/v1/memory/{memory_id}/revoke", json={}, headers=headers_b)
    assert revoke_b.status_code == 404

    # Tenant B's own context never contains tenant A's data.
    listing_b = await client.get("/api/v1/memory", headers=headers_b)
    assert listing_b.json()["memories"] == []
    context_b = await client.get("/api/v1/memory/context", headers=headers_b)
    assert context_b.json()["context"] == []


async def test_read_only_role_cannot_create_memory_over_http(client: AsyncClient) -> None:
    from sqlalchemy import select

    from app.core.security import create_access_token, hash_password
    from app.db.session import async_session_maker
    from app.models.rbac import Role
    from app.models.user import User

    token = await _register(client, "Memory RBAC Co", "owner@memoryrbac.com")
    headers = {"Authorization": f"Bearer {token}"}

    async with async_session_maker() as session:
        owner = (await session.execute(select(User).where(User.email == "owner@memoryrbac.com"))).scalar_one()
        tenant_id = owner.tenant_id
        reader = User(
            tenant_id=tenant_id, email="reader@memoryrbac.com", full_name="Reader",
            hashed_password=hash_password("supersecret1"), role=Role.READ_ONLY,
        )
        session.add(reader)
        await session.commit()
        await session.refresh(reader)
        reader_token = create_access_token(reader.id, tenant_id, Role.READ_ONLY)

    reader_headers = {"Authorization": f"Bearer {reader_token}"}

    # Read-only CAN read.
    listing = await client.get("/api/v1/memory", headers=reader_headers)
    assert listing.status_code == 200

    # Read-only CANNOT create/mutate.
    resp = await client.post(
        "/api/v1/memory",
        json={"memory_type": "OWNER_PREFERENCE", "key": "preferred_appointment_time", "value": "morning"},
        headers=reader_headers,
    )
    assert resp.status_code == 403
    del headers  # only used to register/seed the owner


async def test_confirm_and_reject_pending_memory_over_http(client: AsyncClient) -> None:
    import uuid

    from app.db.session import async_session_maker
    from app.services.company_memory_service import CompanyMemoryService

    token = await _register(client, "Memory Confirm Co", "owner@memoryconfirm.com")
    headers = {"Authorization": f"Bearer {token}"}

    from sqlalchemy import select

    from app.models.user import User

    async with async_session_maker() as session:
        owner = (await session.execute(select(User).where(User.email == "owner@memoryconfirm.com"))).scalar_one()
        tenant_id = owner.tenant_id

    service = CompanyMemoryService(async_session_maker)
    proposed = await service.propose_memory(
        tenant_id, memory_type="OPERATIONAL_PREFERENCE", key="preferred_customer_segment", value="commercial",
        description=None, source_entity_type="morning_brief_recommendation", source_entity_id=uuid.uuid4(),
        confidence=0.8,
    )

    # Not yet in context (PENDING).
    context_before = await client.get("/api/v1/memory/context", headers=headers)
    assert context_before.json()["context"] == []

    confirmed = await client.post(f"/api/v1/memory/{proposed.id}/confirm", headers=headers)
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "ACTIVE"

    context_after = await client.get("/api/v1/memory/context", headers=headers)
    assert len(context_after.json()["context"]) == 1
