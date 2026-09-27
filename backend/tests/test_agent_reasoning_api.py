"""Phase 5: API-level tests for the reasoning-loop evolution of
POST /agents/{id}/execute, and the new GET .../executions/{id} and
GET .../executions/{id}/steps endpoints. No new RBAC permission was
added this phase — EXECUTE_AGENT/READ_AGENT_EXECUTIONS (Phase 4) already
cover the reasoning path.
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


async def _active_agent_over_http(client, owner_token: str, *, name="Reasoner") -> str:
    create_resp = await client.post(
        "/api/v1/agents",
        json={"name": name, "purpose": "test", "autonomy_tier": "EXECUTE_AUTONOMOUS"},
        headers=_auth(owner_token),
    )
    agent_id = create_resp.json()["id"]
    await client.post(
        f"/api/v1/agents/{agent_id}/tool-permissions",
        json={"tool_name": "system.get_tenant_context"}, headers=_auth(owner_token),
    )
    version_resp = await client.post(
        f"/api/v1/agents/{agent_id}/versions",
        json={"instructions": "x", "max_tool_chain_depth": 2}, headers=_auth(owner_token),
    )
    version_id = version_resp.json()["id"]
    await client.post(f"/api/v1/agents/{agent_id}/versions/{version_id}/publish", headers=_auth(owner_token))
    await client.post(f"/api/v1/agents/{agent_id}/activate", headers=_auth(owner_token))
    return agent_id


async def test_execute_requires_exactly_one_of_tool_name_or_goal(client):
    owner_token = await _register(client, "Acme R1", "r1@example.com")
    agent_id = await _active_agent_over_http(client, owner_token)

    neither = await client.post(f"/api/v1/agents/{agent_id}/execute", json={}, headers=_auth(owner_token))
    assert neither.status_code == 422

    both = await client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json={"tool_name": "system.get_tenant_context", "goal": "do something"},
        headers=_auth(owner_token),
    )
    assert both.status_code == 422


async def test_goal_execution_starts_reasoning_mode_and_is_governed(client):
    owner_token = await _register(client, "Acme R2", "r2@example.com")
    agent_id = await _active_agent_over_http(client, owner_token)

    resp = await client.post(
        f"/api/v1/agents/{agent_id}/execute", json={"goal": "Look something up"}, headers=_auth(owner_token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "REASONING"
    assert body["goal"] == "Look something up"
    # No AI provider is configured in the test environment (Settings()
    # under pytest never loads real credentials — see Phase 0's
    # credential-isolation fix) — a REASONING execution must still reach
    # a clean terminal state, never hang RUNNING forever.
    assert body["status"] in ("FAILED", "COMPLETED", "HALTED")
    assert body["termination_reason"] is not None


async def test_execute_agent_requires_execute_agent_permission_for_goal_too(client):
    owner_token = await _register(client, "Acme R3", "r3@example.com")
    tenant_id = await _tenant_id(client, owner_token)
    ro_token = await _readonly_token(client, tenant_id, "ro-r3@example.com")
    agent_id = await _active_agent_over_http(client, owner_token)

    resp = await client.post(
        f"/api/v1/agents/{agent_id}/execute", json={"goal": "g"}, headers=_auth(ro_token)
    )
    assert resp.status_code == 403


async def test_get_execution_and_list_steps(client):
    owner_token = await _register(client, "Acme R4", "r4@example.com")
    agent_id = await _active_agent_over_http(client, owner_token)

    exec_resp = await client.post(
        f"/api/v1/agents/{agent_id}/execute", json={"goal": "g"}, headers=_auth(owner_token)
    )
    execution_id = exec_resp.json()["id"]

    get_resp = await client.get(f"/api/v1/agents/{agent_id}/executions/{execution_id}", headers=_auth(owner_token))
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == execution_id

    steps_resp = await client.get(
        f"/api/v1/agents/{agent_id}/executions/{execution_id}/steps", headers=_auth(owner_token)
    )
    assert steps_resp.status_code == 200
    assert isinstance(steps_resp.json(), list)
    # No step ever exposes a field named for hidden chain-of-thought.
    for step in steps_resp.json():
        assert "chain_of_thought" not in step
        assert "internal_reasoning" not in step


async def test_tenant_isolation_on_execution_and_steps_endpoints(client):
    owner_a = await _register(client, "Tenant A R5", "ra5@example.com")
    owner_b = await _register(client, "Tenant B R5", "rb5@example.com")
    agent_id = await _active_agent_over_http(client, owner_a)

    exec_resp = await client.post(
        f"/api/v1/agents/{agent_id}/execute", json={"goal": "g"}, headers=_auth(owner_a)
    )
    execution_id = exec_resp.json()["id"]

    # Tenant B cannot resume/execute/inspect Tenant A's agent or execution.
    get_resp = await client.get(f"/api/v1/agents/{agent_id}/executions/{execution_id}", headers=_auth(owner_b))
    assert get_resp.status_code == 404

    steps_resp = await client.get(
        f"/api/v1/agents/{agent_id}/executions/{execution_id}/steps", headers=_auth(owner_b)
    )
    assert steps_resp.status_code == 404

    execute_resp = await client.post(
        f"/api/v1/agents/{agent_id}/execute", json={"goal": "g"}, headers=_auth(owner_b)
    )
    assert execute_resp.status_code == 409  # agent not found for tenant B -> not executable


async def test_idempotency_key_dedupes_over_http_for_reasoning(client):
    owner_token = await _register(client, "Acme R6", "r6@example.com")
    agent_id = await _active_agent_over_http(client, owner_token)

    first = await client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json={"goal": "g", "idempotency_key": "same-key"}, headers=_auth(owner_token),
    )
    second = await client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json={"goal": "g different text", "idempotency_key": "same-key"}, headers=_auth(owner_token),
    )
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]


async def test_single_action_execute_still_works_unchanged(client):
    """Backward-compatible evolution check: an existing tool_name-only
    client (Phase 4 shape) still works exactly as before."""
    owner_token = await _register(client, "Acme R7", "r7@example.com")
    agent_id = await _active_agent_over_http(client, owner_token)

    resp = await client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json={"tool_name": "system.get_tenant_context", "tool_input": {}}, headers=_auth(owner_token),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "COMPLETED"
    assert resp.json()["mode"] == "SINGLE_ACTION"
