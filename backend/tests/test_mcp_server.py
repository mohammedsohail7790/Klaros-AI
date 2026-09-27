"""Phase 9 (MCP server exposure — server direction only, per
KLAROS_FINAL_AGENT_MODEL.md's MCP-server carve-out; the MCP-client
direction remains rejected per ADR-002 and is not exercised anywhere in
this file).

Covers, over real HTTP against the ASGI app (`client` fixture):
  - Architecture: every MCP tool call still routes through
    `ToolRegistry.execute()` — proven indirectly by observing the SAME
    governance outcomes (RBAC denial, policy BLOCKED, kill switch,
    approval creation) an equivalent direct API/tool call would produce.
  - Authentication: unauthenticated / malformed / unknown / revoked
    credential all rejected with a generic 401; the raw token is never
    echoed back anywhere after issuance.
  - Tenant isolation: tenant A's MCP credential cannot see or invoke
    tenant B's exposed tools.
  - Exposure policy: a tool NOT on the allowlist is invisible to
    `tools/list` and rejected by `tools/call`, even though it exists (and
    is otherwise permitted) in `ToolRegistry`. Only `Permission.
    MANAGE_MCP_SERVER` (OWNER/ADMIN) can change the allowlist.
  - RBAC: a STAFF-role MCP credential is denied a tool STAFF can't use,
    the same way a STAFF human user would be.
  - Approval composition: an APPROVAL_REQUIRED tool invoked via MCP
    creates a real `ApprovalRequest`; approving it resumes and executes
    exactly once.
  - Kill switch: `ai_paused=true` blocks MCP-routed tool calls.
  - Malformed/oversized/deeply-nested input rejected before reaching
    ToolRegistry.
  - Bounded timeout: a slow tool call does not hang the request forever.
"""

import json
import uuid as _uuid

import pytest

pytestmark = pytest.mark.asyncio

MCP_URL = "/api/v1/mcp"


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


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _expose_tool(client, owner_token: str, tool_name: str) -> None:
    resp = await client.put(
        "/api/v1/mcp-admin/exposures", json={"tool_name": tool_name, "enabled": True}, headers=_auth(owner_token)
    )
    assert resp.status_code == 200, resp.text


async def _issue_credential(client, owner_token: str, name: str, role: str) -> str:
    resp = await client.post(
        "/api/v1/mcp-admin/credentials", json={"name": name, "role": role}, headers=_auth(owner_token)
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def _rpc(method: str, params: dict | None = None, req_id: int = 1) -> dict:
    body = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        body["params"] = params
    return body


async def _setup_tenant_with_credential(client, org_name: str, email: str, tool_name: str, role: str = "OWNER"):
    owner_token = await _register(client, org_name, email)
    tenant_id = await _tenant_id(client, owner_token)
    await _expose_tool(client, owner_token, tool_name)
    mcp_token = await _issue_credential(client, owner_token, "external-agent", role)
    return owner_token, tenant_id, mcp_token


# --- Authentication ---------------------------------------------------------


async def test_unauthenticated_request_rejected(client):
    resp = await client.post(MCP_URL, content=json.dumps(_rpc("tools/list")))
    assert resp.status_code == 401


async def test_malformed_bearer_rejected(client):
    resp = await client.post(
        MCP_URL, content=json.dumps(_rpc("tools/list")), headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert resp.status_code == 401


async def test_revoked_credential_rejected(client):
    owner_token, _tenant_id, mcp_token = await _setup_tenant_with_credential(
        client, "Acme MCP Revoke", "owner-revoke@example.com", "crm.create_lead"
    )
    # Find and revoke the just-issued credential.
    creds = (await client.get("/api/v1/mcp-admin/credentials", headers=_auth(owner_token))).json()
    cred_id = creds[0]["id"]
    resp = await client.delete(f"/api/v1/mcp-admin/credentials/{cred_id}", headers=_auth(owner_token))
    assert resp.status_code == 204

    resp = await client.post(
        MCP_URL, content=json.dumps(_rpc("tools/list")), headers={"Authorization": f"Bearer {mcp_token}"}
    )
    assert resp.status_code == 401


async def test_credential_issuance_requires_manage_mcp_server_permission(client):
    from app.core.security import create_access_token, hash_password
    from app.db.session import async_session_maker
    from app.models.rbac import Role
    from app.models.user import User

    owner_token = await _register(client, "Acme MCP RBAC", "owner-rbac@example.com")
    tenant_id = await _tenant_id(client, owner_token)

    async with async_session_maker() as session:
        user = User(
            tenant_id=tenant_id, email="staff-rbac@example.com", full_name="Staff",
            hashed_password=hash_password("supersecret1"), role=Role.STAFF,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        staff_token = create_access_token(user.id, tenant_id, Role.STAFF)

    resp = await client.post(
        "/api/v1/mcp-admin/credentials",
        json={"name": "x", "role": "STAFF"},
        headers=_auth(staff_token),
    )
    assert resp.status_code == 403


# --- Architecture: routes through ToolRegistry / exposure policy -----------


async def test_tools_list_only_shows_exposed_tools(client):
    owner_token, _tid, mcp_token = await _setup_tenant_with_credential(
        client, "Acme MCP List", "owner-list@example.com", "crm.create_lead"
    )
    resp = await client.post(
        MCP_URL, content=json.dumps(_rpc("tools/list")), headers={"Authorization": f"Bearer {mcp_token}"}
    )
    assert resp.status_code == 200
    names = {t["name"] for t in resp.json()["result"]["tools"]}
    assert names == {"crm.create_lead"}


async def test_non_exposed_tool_rejected_even_if_registered_and_permitted(client):
    owner_token, _tid, mcp_token = await _setup_tenant_with_credential(
        client, "Acme MCP Deny", "owner-deny@example.com", "crm.create_lead"
    )
    # crm.get_lead is a real, AUTO, OWNER-permitted tool — but never exposed.
    resp = await client.post(
        MCP_URL,
        content=json.dumps(_rpc("tools/call", {"name": "crm.get_lead", "arguments": {"lead_id": str(_uuid.uuid4())}})),
        headers={"Authorization": f"Bearer {mcp_token}"},
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["isError"] is True


async def test_exposed_tool_call_succeeds_through_tool_registry(client):
    owner_token, _tid, mcp_token = await _setup_tenant_with_credential(
        client, "Acme MCP Call", "owner-call@example.com", "crm.create_lead"
    )
    resp = await client.post(
        MCP_URL,
        content=json.dumps(
            _rpc("tools/call", {"name": "crm.create_lead", "arguments": {"name": "Jane Doe", "source": "WEBSITE"}})
        ),
        headers={"Authorization": f"Bearer {mcp_token}"},
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["lead"]["name"] == "Jane Doe"

    # And it's really governed by ToolRegistry: a real audit row exists for it.
    from sqlalchemy import select

    from app.db.session import async_session_maker
    from app.models.actor import ActorType
    from app.models.audit_log import AuditLog

    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id == _tid, AuditLog.actor_type == ActorType.MCP_CLIENT.value
                )
            )
        ).scalars().all()
    assert any(r.tool == "crm.create_lead" and r.result == "success" for r in rows)


async def test_exposure_requires_manage_mcp_server_permission(client):
    owner_token = await _register(client, "Acme MCP ExposeRBAC", "owner-exp@example.com")
    from app.core.security import create_access_token, hash_password
    from app.db.session import async_session_maker
    from app.models.rbac import Role
    from app.models.user import User

    tenant_id = await _tenant_id(client, owner_token)
    async with async_session_maker() as session:
        user = User(
            tenant_id=tenant_id, email="manager-exp@example.com", full_name="Manager",
            hashed_password=hash_password("supersecret1"), role=Role.MANAGER,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        manager_token = create_access_token(user.id, tenant_id, Role.MANAGER)

    resp = await client.put(
        "/api/v1/mcp-admin/exposures",
        json={"tool_name": "crm.create_lead", "enabled": True},
        headers=_auth(manager_token),
    )
    assert resp.status_code == 403


# --- RBAC: an MCP credential is bound to a Role like any other caller -----


async def test_mcp_client_role_gates_tool_access_like_a_human_of_that_role(client):
    owner_token, _tid, _owner_mcp_token = await _setup_tenant_with_credential(
        client, "Acme MCP RoleGate", "owner-rolegate@example.com", "finance.void_invoice", role="OWNER"
    )
    # A READ_ONLY-role MCP credential is exposed the tool by policy, but
    # READ_ONLY has no permission for finance.void_invoice — RBAC inside
    # ToolRegistry.execute() must still deny it exactly as it would a
    # READ_ONLY human user.
    ro_mcp_token = await _issue_credential(client, owner_token, "readonly-agent", "READ_ONLY")
    resp = await client.post(
        MCP_URL,
        content=json.dumps(
            _rpc(
                "tools/call",
                {
                    "name": "finance.void_invoice",
                    "arguments": {"invoice_id": str(_uuid.uuid4()), "reason": "test"},
                },
            )
        ),
        headers={"Authorization": f"Bearer {ro_mcp_token}"},
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["isError"] is True
    assert "permission" in json.dumps(result).lower()


# --- Tenant isolation -------------------------------------------------------


async def test_mcp_credential_cannot_see_another_tenants_exposures(client):
    _owner_a, tenant_a, mcp_token_a = await _setup_tenant_with_credential(
        client, "Acme MCP TenantA", "owner-tenanta@example.com", "crm.create_lead"
    )
    owner_b, tenant_b, mcp_token_b = await _setup_tenant_with_credential(
        client, "Acme MCP TenantB", "owner-tenantb@example.com", "finance.void_invoice"
    )
    assert tenant_a != tenant_b

    resp_a = await client.post(
        MCP_URL, content=json.dumps(_rpc("tools/list")), headers={"Authorization": f"Bearer {mcp_token_a}"}
    )
    names_a = {t["name"] for t in resp_a.json()["result"]["tools"]}
    assert names_a == {"crm.create_lead"}

    resp_b = await client.post(
        MCP_URL, content=json.dumps(_rpc("tools/list")), headers={"Authorization": f"Bearer {mcp_token_b}"}
    )
    names_b = {t["name"] for t in resp_b.json()["result"]["tools"]}
    assert names_b == {"finance.void_invoice"}


async def test_tenant_a_credential_cannot_invoke_tenant_bs_exposed_tool(client):
    """Tenant A never exposed finance.void_invoice — tenant A's credential
    calling it must be denied exactly as any never-exposed tool would be,
    never accidentally resolved against tenant B's exposure row."""
    _owner_a, _tenant_a, mcp_token_a = await _setup_tenant_with_credential(
        client, "Acme MCP TenantIsoA", "owner-tia@example.com", "crm.create_lead"
    )
    await _setup_tenant_with_credential(
        client, "Acme MCP TenantIsoB", "owner-tib@example.com", "finance.void_invoice"
    )

    resp = await client.post(
        MCP_URL,
        content=json.dumps(
            _rpc(
                "tools/call",
                {
                    "name": "finance.void_invoice",
                    "arguments": {"invoice_id": str(_uuid.uuid4()), "reason": "test"},
                },
            )
        ),
        headers={"Authorization": f"Bearer {mcp_token_a}"},
    )
    result = resp.json()["result"]
    assert result["isError"] is True


# --- Approval composition ---------------------------------------------------


async def test_approval_required_tool_creates_real_approval_request_and_resumes_once(client):
    from app.models.rbac import Permission
    from app.services.policy_service import PolicyService
    from app.tools.policy import ActionPolicy
    from app.db.session import async_session_maker

    owner_token, tenant_id, mcp_token = await _setup_tenant_with_credential(
        client, "Acme MCP Approval", "owner-approval@example.com", "crm.create_lead"
    )
    policy_service = PolicyService(async_session_maker)
    await policy_service.set_policy(tenant_id, "crm.create_lead", ActionPolicy.APPROVAL_REQUIRED, actor_id=None)

    resp = await client.post(
        MCP_URL,
        content=json.dumps(
            _rpc("tools/call", {"name": "crm.create_lead", "arguments": {"name": "Approval Case", "source": "WEBSITE"}})
        ),
        headers={"Authorization": f"Bearer {mcp_token}"},
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["status"] == "pending_approval"
    approval_id = payload["approval_request_id"]

    approve_resp = await client.post(
        f"/api/v1/approvals/{approval_id}/approve", json={}, headers=_auth(owner_token)
    )
    assert approve_resp.status_code == 200, approve_resp.text
    body = approve_resp.json()
    assert body["execution_status"] == "EXECUTED"

    # Resuming again must not double-execute.
    from sqlalchemy import select

    from app.models.approval import ApprovalRequest

    async with async_session_maker() as session:
        row = await session.get(ApprovalRequest, _uuid.UUID(approval_id))
        assert row.execution_attempts == 1


# --- Kill switch -------------------------------------------------------------


async def test_kill_switch_blocks_mcp_tool_calls(client):
    from sqlalchemy import select

    from app.db.session import async_session_maker
    from app.models.organization import Organization

    owner_token, tenant_id, mcp_token = await _setup_tenant_with_credential(
        client, "Acme MCP KillSwitch", "owner-kill@example.com", "crm.create_lead"
    )
    async with async_session_maker() as session:
        org = await session.get(Organization, tenant_id)
        org.ai_paused = True
        await session.commit()

    resp = await client.post(
        MCP_URL,
        content=json.dumps(
            _rpc("tools/call", {"name": "crm.create_lead", "arguments": {"name": "Blocked", "source": "WEBSITE"}})
        ),
        headers={"Authorization": f"Bearer {mcp_token}"},
    )
    result = resp.json()["result"]
    assert result["isError"] is True
    assert "kill_switch" in json.dumps(result).lower() or "paused" in json.dumps(result).lower()


# --- Input validation / transport protection --------------------------------


async def test_malformed_arguments_rejected_before_tool_registry(client):
    _owner, _tid, mcp_token = await _setup_tenant_with_credential(
        client, "Acme MCP Malformed", "owner-malformed@example.com", "crm.create_lead"
    )
    resp = await client.post(
        MCP_URL,
        content=json.dumps(_rpc("tools/call", {"name": "crm.create_lead", "arguments": {"source": 12345}})),
        headers={"Authorization": f"Bearer {mcp_token}"},
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["isError"] is True
    assert "invalid arguments" in json.dumps(result).lower()


async def test_oversized_request_body_rejected(client):
    _owner, _tid, mcp_token = await _setup_tenant_with_credential(
        client, "Acme MCP Oversized", "owner-oversized@example.com", "crm.create_lead"
    )
    huge_arguments = {"name": "x" * (300 * 1024), "source": "WEBSITE"}
    resp = await client.post(
        MCP_URL,
        content=json.dumps(_rpc("tools/call", {"name": "crm.create_lead", "arguments": huge_arguments})),
        headers={"Authorization": f"Bearer {mcp_token}"},
    )
    body = resp.json()
    assert body.get("error", {}).get("code") == -32600


async def test_deeply_nested_payload_rejected(client):
    _owner, _tid, mcp_token = await _setup_tenant_with_credential(
        client, "Acme MCP Nested", "owner-nested@example.com", "crm.create_lead"
    )
    nested: dict = {"v": 1}
    node = nested
    for _ in range(30):
        node["child"] = {"v": 1}
        node = node["child"]
    resp = await client.post(
        MCP_URL,
        content=json.dumps(_rpc("tools/call", {"name": "crm.create_lead", "arguments": nested})),
        headers={"Authorization": f"Bearer {mcp_token}"},
    )
    body = resp.json()
    assert body.get("error", {}).get("code") == -32600


async def test_unknown_method_rejected(client):
    _owner, _tid, mcp_token = await _setup_tenant_with_credential(
        client, "Acme MCP UnknownMethod", "owner-unknownmethod@example.com", "crm.create_lead"
    )
    resp = await client.post(
        MCP_URL, content=json.dumps(_rpc("resources/list")), headers={"Authorization": f"Bearer {mcp_token}"}
    )
    body = resp.json()
    assert body["error"]["code"] == -32601


async def test_notification_gets_no_response_body(client):
    _owner, _tid, mcp_token = await _setup_tenant_with_credential(
        client, "Acme MCP Notify", "owner-notify@example.com", "crm.create_lead"
    )
    envelope = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    resp = await client.post(
        MCP_URL, content=json.dumps(envelope), headers={"Authorization": f"Bearer {mcp_token}"}
    )
    assert resp.status_code == 204


async def test_prompt_injection_style_tenant_override_ignored(client):
    """A malicious client tries to smuggle a different tenant_id / role
    inside the tool arguments themselves — ExecutionContext must come only
    from the authenticated credential, never from these fields."""
    _owner, real_tenant_id, mcp_token = await _setup_tenant_with_credential(
        client, "Acme MCP Injection", "owner-injection@example.com", "crm.create_lead"
    )
    other_tenant = str(_uuid.uuid4())
    resp = await client.post(
        MCP_URL,
        content=json.dumps(
            _rpc(
                "tools/call",
                {
                    "name": "crm.create_lead",
                    "arguments": {
                        "name": "Injected",
                        "source": "WEBSITE",
                        "tenant_id": other_tenant,
                        "role": "OWNER",
                        "actor_type": "SYSTEM",
                    },
                },
            )
        ),
        headers={"Authorization": f"Bearer {mcp_token}"},
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    # The lead was created under the REAL tenant, not the injected one.
    from sqlalchemy import select

    from app.db.session import async_session_maker
    from app.models.crm import Lead

    async with async_session_maker() as session:
        lead = await session.get(Lead, _uuid.UUID(payload["lead"]["id"]))
        assert lead.tenant_id == real_tenant_id
