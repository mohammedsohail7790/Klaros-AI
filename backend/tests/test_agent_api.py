"""Phase 4: API-level tests for /agents — authentication, RBAC (wrong role
rejected), tenant isolation over HTTP, lifecycle action endpoints (never a
generic PATCH), and a full happy-path walk: create -> grant tool -> create
version -> publish -> activate -> execute -> list executions.
"""

import uuid as _uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _register(client, org_name: str, email: str) -> str:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"organization_name": org_name, "full_name": "Owner Test", "email": email, "password": "supersecret1"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["tokens"]["access_token"]


async def _tenant_id(client, token: str) -> _uuid.UUID:
    resp = await client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    return _uuid.UUID(resp.json()["tenant_id"])


async def _readonly_token(client, tenant_id: _uuid.UUID, email: str) -> str:
    from app.core.security import create_access_token, hash_password
    from app.db.session import async_session_maker
    from app.models.rbac import Role
    from app.models.user import User

    async with async_session_maker() as session:
        user = User(
            tenant_id=tenant_id, email=email, full_name="Read Only User",
            hashed_password=hash_password("supersecret1"), role=Role.READ_ONLY,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return create_access_token(user.id, tenant_id, Role.READ_ONLY)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_unauthenticated_request_rejected(client):
    resp = await client.get("/api/v1/agents")
    assert resp.status_code == 401


async def test_create_agent_requires_manage_agents(client):
    owner_token = await _register(client, "Acme Agents", "owner1@example.com")
    tenant_id = await _tenant_id(client, owner_token)
    ro_token = await _readonly_token(client, tenant_id, "ro1@example.com")

    resp = await client.post(
        "/api/v1/agents", json={"name": "Bot", "purpose": "x"}, headers=_auth(ro_token)
    )
    assert resp.status_code == 403


async def test_full_agent_lifecycle_over_http(client):
    owner_token = await _register(client, "Acme Full", "owner2@example.com")

    create_resp = await client.post(
        "/api/v1/agents",
        json={"name": "Lead Bot", "purpose": "qualify leads", "autonomy_tier": "EXECUTE_AUTONOMOUS"},
        headers=_auth(owner_token),
    )
    assert create_resp.status_code == 201, create_resp.text
    agent_id = create_resp.json()["id"]
    assert create_resp.json()["status"] == "DRAFT"

    grant_resp = await client.post(
        f"/api/v1/agents/{agent_id}/tool-permissions",
        json={"tool_name": "system.get_tenant_context"},
        headers=_auth(owner_token),
    )
    assert grant_resp.status_code == 201, grant_resp.text

    bad_grant_resp = await client.post(
        f"/api/v1/agents/{agent_id}/tool-permissions",
        json={"tool_name": "does.not.exist"},
        headers=_auth(owner_token),
    )
    assert bad_grant_resp.status_code == 422

    version_resp = await client.post(
        f"/api/v1/agents/{agent_id}/versions",
        json={"instructions": "Qualify leads politely."},
        headers=_auth(owner_token),
    )
    assert version_resp.status_code == 201, version_resp.text
    version_id = version_resp.json()["id"]

    publish_resp = await client.post(
        f"/api/v1/agents/{agent_id}/versions/{version_id}/publish", headers=_auth(owner_token)
    )
    assert publish_resp.status_code == 200
    assert publish_resp.json()["status"] == "PUBLISHED"

    activate_resp = await client.post(f"/api/v1/agents/{agent_id}/activate", headers=_auth(owner_token))
    assert activate_resp.status_code == 200
    assert activate_resp.json()["status"] == "ACTIVE"

    execute_resp = await client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json={"tool_name": "system.get_tenant_context", "tool_input": {}},
        headers=_auth(owner_token),
    )
    assert execute_resp.status_code == 200, execute_resp.text
    assert execute_resp.json()["status"] == "COMPLETED"

    executions_resp = await client.get(f"/api/v1/agents/{agent_id}/executions", headers=_auth(owner_token))
    assert executions_resp.status_code == 200
    assert len(executions_resp.json()) == 1

    pause_resp = await client.post(f"/api/v1/agents/{agent_id}/pause", headers=_auth(owner_token))
    assert pause_resp.status_code == 200
    assert pause_resp.json()["status"] == "PAUSED"

    archive_resp = await client.post(f"/api/v1/agents/{agent_id}/archive", headers=_auth(owner_token))
    assert archive_resp.status_code == 200
    assert archive_resp.json()["status"] == "ARCHIVED"

    reactivate_resp = await client.post(f"/api/v1/agents/{agent_id}/activate", headers=_auth(owner_token))
    assert reactivate_resp.status_code == 409


async def test_execute_requires_execute_agent_permission(client):
    owner_token = await _register(client, "Acme Exec Perm", "owner3@example.com")
    tenant_id = await _tenant_id(client, owner_token)
    ro_token = await _readonly_token(client, tenant_id, "ro3@example.com")

    create_resp = await client.post(
        "/api/v1/agents", json={"name": "Bot", "purpose": "x", "autonomy_tier": "EXECUTE_AUTONOMOUS"},
        headers=_auth(owner_token),
    )
    agent_id = create_resp.json()["id"]

    resp = await client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json={"tool_name": "system.get_tenant_context", "tool_input": {}},
        headers=_auth(ro_token),
    )
    assert resp.status_code == 403


async def test_tenant_isolation_over_http(client):
    owner_a = await _register(client, "Tenant A Agents", "ownerA@example.com")
    owner_b = await _register(client, "Tenant B Agents", "ownerB@example.com")

    create_resp = await client.post(
        "/api/v1/agents", json={"name": "A's Bot", "purpose": "x"}, headers=_auth(owner_a)
    )
    agent_id = create_resp.json()["id"]

    get_resp = await client.get(f"/api/v1/agents/{agent_id}", headers=_auth(owner_b))
    assert get_resp.status_code == 404

    list_resp = await client.get("/api/v1/agents", headers=_auth(owner_b))
    assert all(a["id"] != agent_id for a in list_resp.json())

    execute_resp = await client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json={"tool_name": "system.get_tenant_context", "tool_input": {}},
        headers=_auth(owner_b),
    )
    assert execute_resp.status_code == 409


async def test_lifecycle_uses_explicit_action_endpoints_not_patch(client):
    owner_token = await _register(client, "Acme No Patch", "owner4@example.com")
    create_resp = await client.post(
        "/api/v1/agents", json={"name": "Bot", "purpose": "x"}, headers=_auth(owner_token)
    )
    agent_id = create_resp.json()["id"]
    resp = await client.patch(f"/api/v1/agents/{agent_id}", json={"status": "ACTIVE"}, headers=_auth(owner_token))
    assert resp.status_code in (404, 405)
