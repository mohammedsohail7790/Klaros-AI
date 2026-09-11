"""Phase 21: verifies the data the owner-facing Approvals and Company
Memory UI surfaces actually depend on is real and correctly exposed by the
existing APIs — this phase deliberately makes ZERO backend changes (every
field the frontend needed — `requested_by_type` on `ApprovalRow`,
`source_entity_type`/`source_entity_id` on `CompanyMemoryRow` — already
existed). These tests exercise the exact real HTTP endpoints the new UI
calls, plus the RBAC boundary the UI relies on the backend (never the
browser) to enforce.
"""

import json
import uuid

import pytest

from app.core.security import create_access_token, hash_password
from app.models.rbac import Role
from app.models.user import User

pytestmark = pytest.mark.asyncio


async def _create_second_user(async_session_maker, tenant_id: uuid.UUID, *, role: Role) -> str:
    async with async_session_maker() as session:
        user = User(
            tenant_id=tenant_id, email=f"{uuid.uuid4().hex[:8]}@phase21.com",
            hashed_password=hash_password("supersecret1"), full_name="Second User", role=role,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return create_access_token(user.id, tenant_id, role.value)


async def _register(client, org: str, email: str) -> tuple[str, uuid.UUID]:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"organization_name": org, "full_name": "Owner", "email": email, "password": "supersecret1"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["tokens"]["access_token"], uuid.UUID(resp.json()["user"]["tenant_id"])


async def _make_ai_originated_approval(async_session_maker, tool_registry, tenant_id: uuid.UUID) -> uuid.UUID:
    """A real AI-originated APPROVAL_REQUIRED ApprovalRequest, using the
    same real AI Next Action pipeline Phase 18-20 already proved — not a
    fabricated row inserted directly."""
    import json as _json
    from datetime import date, timedelta

    from app.ai.execution_service import AIExecutionService
    from app.models.crm import Customer, CustomerStatus
    from app.models.quote import Quote, QuoteStatus
    from app.models.tool_policy import TenantToolPolicy
    from app.services.ai_next_action_service import AINextActionService
    from app.services.ai_provider import AICallOutcome
    from app.services.policy_service import ActionPolicy

    class _FakeProvider:
        is_connected = True
        name = "fake"
        model = "fake-model-1"

        async def generate_structured(self, prompt: str):
            payload = {
                "tool_name": "notifications.create_notification", "reason": "Quote expired, no response.",
                "confidence": 0.8, "arguments": {"title": "Quote expired", "body": "Needs follow-up."},
            }
            return AICallOutcome(success=True, provider=self.name, model=self.model, latency_ms=5, raw_text=_json.dumps(payload))

    from sqlalchemy import select

    async with async_session_maker() as session:
        existing_policy = (
            await session.execute(
                select(TenantToolPolicy).where(
                    TenantToolPolicy.tenant_id == tenant_id, TenantToolPolicy.tool_name == "notifications.create_notification",
                )
            )
        ).scalar_one_or_none()
        if existing_policy is None:
            session.add(TenantToolPolicy(tenant_id=tenant_id, tool_name="notifications.create_notification", policy=ActionPolicy.APPROVAL_REQUIRED, enabled=True))
        customer = Customer(tenant_id=tenant_id, name="UI Surface Customer", status=CustomerStatus.ACTIVE)
        session.add(customer)
        await session.flush()
        quote = Quote(
            tenant_id=tenant_id, quote_number=f"Q-UI-{uuid.uuid4().hex[:8]}", customer_id=customer.id,
            status=QuoteStatus.EXPIRED, currency="USD", subtotal=500, tax=0, discount=0, total=500,
            valid_until=date.today() - timedelta(days=1),
        )
        session.add(quote)
        await session.commit()
        quote_id = quote.id

    service = AINextActionService(async_session_maker, AIExecutionService(tool_registry), _FakeProvider())
    decision = await service.decide_quote_followup(tenant_id, quote_id, correlation_id=uuid.uuid4())
    assert decision.outcome == "approval_required"
    return decision.approval_request_id


# --- A/B: AI-originated approval visible with clear origin -----------------

async def test_ai_originated_approval_visible_via_real_list_and_detail_endpoints(client, tool_registry) -> None:
    from app.db.session import async_session_maker

    token, tenant_id = await _register(client, "Phase21 Approval Co", "phase21-approval@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    approval_id = await _make_ai_originated_approval(async_session_maker, tool_registry, tenant_id)

    listed = await client.get("/api/v1/approvals", params={"status_filter": "PENDING"}, headers=headers)
    assert listed.status_code == 200, listed.text
    row = next(a for a in listed.json()["approvals"] if a["id"] == str(approval_id))
    assert row["requested_by_type"] == "AI"  # A/B: real, not fabricated — this is exactly what the badge renders from

    detail = await client.get(f"/api/v1/approvals/{approval_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["requested_by_type"] == "AI"
    assert detail.json()["tool_name"] == "notifications.create_notification"
    assert detail.json()["status"] == "PENDING"


# --- C/D/E: approve/reject use the real existing endpoints, note preserved -

async def test_approve_via_real_endpoint_executes_and_reflects_state(client, tool_registry) -> None:
    from app.db.session import async_session_maker

    token, tenant_id = await _register(client, "Phase21 Approve Co", "phase21-approve@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    approval_id = await _make_ai_originated_approval(async_session_maker, tool_registry, tenant_id)

    resp = await client.post(f"/api/v1/approvals/{approval_id}/approve", json={"decision_note": None}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "APPROVED"
    assert resp.json()["execution_status"] == "EXECUTED"  # Rule 9: only claim execution if the backend actually confirms it


async def test_reject_via_real_endpoint_preserves_note_and_never_executes(client, tool_registry) -> None:
    from app.db.session import async_session_maker
    from sqlalchemy import select

    from app.models.notification import Notification

    token, tenant_id = await _register(client, "Phase21 Reject Co", "phase21-reject@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    approval_id = await _make_ai_originated_approval(async_session_maker, tool_registry, tenant_id)

    resp = await client.post(
        f"/api/v1/approvals/{approval_id}/reject", json={"decision_note": "Not needed, already handled."}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "REJECTED"

    detail = await client.get(f"/api/v1/approvals/{approval_id}", headers=headers)
    assert detail.json()["decision_note"] == "Not needed, already handled."

    async with async_session_maker() as session:
        rows = (await session.execute(select(Notification).where(Notification.tenant_id == tenant_id))).scalars().all()
    assert rows == []


# --- F/G/H/I/J: AI_FEEDBACK visible, labeled, confirm produces real ACTIVE -

async def test_ai_feedback_visible_labeled_and_confirm_produces_real_active_state(client, tool_registry) -> None:
    from app.db.session import async_session_maker

    token, tenant_id = await _register(client, "Phase21 Feedback Co", "phase21-feedback@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    approval_id = await _make_ai_originated_approval(async_session_maker, tool_registry, tenant_id)
    await client.post(f"/api/v1/approvals/{approval_id}/approve", json={}, headers=headers)

    # F: appears via the real memory list endpoint, filterable exactly as the UI does.
    listed = await client.get("/api/v1/memory", params={"memory_type": "AI_FEEDBACK"}, headers=headers)
    assert listed.status_code == 200
    memories = listed.json()["memories"]
    assert len(memories) == 1
    feedback = memories[0]

    # G: clearly AI-originated (source=AI_PROPOSED, memory_type=AI_FEEDBACK
    # — exactly what the frontend badge/label condition checks).
    assert feedback["memory_type"] == "AI_FEEDBACK"
    assert feedback["source"] == "AI_PROPOSED"
    assert feedback["status"] == "PENDING"
    # Provenance the "View the approval" link relies on:
    assert feedback["source_entity_type"] == "approval_request"
    assert feedback["source_entity_id"] == str(approval_id)

    # H/I: the real confirm endpoint produces real ACTIVE state.
    confirm = await client.post(f"/api/v1/memory/{feedback['id']}/confirm", json={}, headers=headers)
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["status"] == "ACTIVE"

    refetched = await client.get("/api/v1/memory", params={"memory_type": "AI_FEEDBACK", "status_filter": "ACTIVE"}, headers=headers)
    assert any(m["id"] == feedback["id"] and m["status"] == "ACTIVE" for m in refetched.json()["memories"])


async def test_confirming_an_already_decided_memory_fails_honestly_not_fake_active(client, tool_registry) -> None:
    from app.db.session import async_session_maker

    token, tenant_id = await _register(client, "Phase21 DoubleConfirm Co", "phase21-doubleconfirm@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    approval_id = await _make_ai_originated_approval(async_session_maker, tool_registry, tenant_id)
    await client.post(f"/api/v1/approvals/{approval_id}/approve", json={}, headers=headers)
    listed = await client.get("/api/v1/memory", params={"memory_type": "AI_FEEDBACK"}, headers=headers)
    memory_id = listed.json()["memories"][0]["id"]

    first = await client.post(f"/api/v1/memory/{memory_id}/confirm", json={}, headers=headers)
    assert first.status_code == 200
    assert first.json()["status"] == "ACTIVE"

    # J: a second confirm attempt on an already-ACTIVE memory must fail
    # honestly (409/422), never silently report success/ACTIVE again.
    second = await client.post(f"/api/v1/memory/{memory_id}/confirm", json={}, headers=headers)
    assert second.status_code in (409, 422), second.text


# --- K: RBAC — unauthorized roles cannot mutate ----------------------------

async def test_technician_cannot_approve_or_confirm_ai_decisions(client, tool_registry) -> None:
    from app.db.session import async_session_maker

    token, tenant_id = await _register(client, "Phase21 RBAC Co", "phase21-rbac@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    approval_id = await _make_ai_originated_approval(async_session_maker, tool_registry, tenant_id)
    await client.post(f"/api/v1/approvals/{approval_id}/approve", json={}, headers=headers)
    memory_id = (
        await client.get("/api/v1/memory", params={"memory_type": "AI_FEEDBACK"}, headers=headers)
    ).json()["memories"][0]["id"]

    # A fresh PENDING approval for the technician-approve attempt.
    approval_id_2 = await _make_ai_originated_approval(async_session_maker, tool_registry, tenant_id)

    technician_token = await _create_second_user(async_session_maker, tenant_id, role=Role.TECHNICIAN)
    technician_headers = {"Authorization": f"Bearer {technician_token}"}

    approve_resp = await client.post(f"/api/v1/approvals/{approval_id_2}/approve", json={}, headers=technician_headers)
    # The RBAC check itself is real and correct ("Actor lacks required
    # permission: APPROVE_ACTIONS") — a bare ValueError from inside the
    # tool maps to 404 via this codebase's existing generic tool-error
    # handler (pre-existing convention, not weakened or changed here).
    assert approve_resp.status_code in (400, 403, 404, 422), approve_resp.text
    assert "APPROVE_ACTIONS" in approve_resp.text

    async with async_session_maker() as session:
        from app.models.approval import ApprovalRequest, ApprovalStatus

        still_pending = await session.get(ApprovalRequest, approval_id_2)
        assert still_pending.status == ApprovalStatus.PENDING  # never approved despite the attempt

    confirm_resp = await client.post(f"/api/v1/memory/{memory_id}/confirm", json={}, headers=technician_headers)
    assert confirm_resp.status_code == 403, confirm_resp.text

    # TECHNICIAN also lacks READ_APPROVALS in this codebase's existing role
    # table (only OWNER/MANAGER/READ_ONLY do) — viewing is correctly denied
    # too, not just mutation. Confirms this phase changed no RBAC behavior.
    view_resp = await client.get("/api/v1/approvals", headers=technician_headers)
    assert view_resp.status_code in (403, 404)

    readonly_token = await _create_second_user(async_session_maker, tenant_id, role=Role.READ_ONLY)
    readonly_view = await client.get("/api/v1/approvals", headers={"Authorization": f"Bearer {readonly_token}"})
    assert readonly_view.status_code == 200  # READ_ONLY genuinely can view, per the existing role table

    readonly_approve = await client.post(
        f"/api/v1/approvals/{approval_id_2}/approve", json={}, headers={"Authorization": f"Bearer {readonly_token}"}
    )
    assert readonly_approve.status_code in (400, 403, 404, 422)  # but still cannot approve


# --- L: tenant isolation ---------------------------------------------------

async def test_tenant_isolation_of_approvals_and_feedback(client, tool_registry) -> None:
    from app.db.session import async_session_maker

    token_a, tenant_a = await _register(client, "Phase21 Tenant A", "phase21-tenant-a@example.com")
    token_b, tenant_b = await _register(client, "Phase21 Tenant B", "phase21-tenant-b@example.com")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    approval_a = await _make_ai_originated_approval(async_session_maker, tool_registry, tenant_a)

    # Tenant B cannot see or act on tenant A's approval.
    cross_detail = await client.get(f"/api/v1/approvals/{approval_a}", headers=headers_b)
    assert cross_detail.status_code == 404
    cross_approve = await client.post(f"/api/v1/approvals/{approval_a}/approve", json={}, headers=headers_b)
    assert cross_approve.status_code in (403, 404, 422)

    listed_b = await client.get("/api/v1/approvals", headers=headers_b)
    assert all(a["id"] != str(approval_a) for a in listed_b.json()["approvals"])

    # Approve on tenant A -> confirm -> tenant B must never see the feedback memory.
    await client.post(f"/api/v1/approvals/{approval_a}/approve", json={}, headers=headers_a)
    listed_b_memory = await client.get("/api/v1/memory", params={"memory_type": "AI_FEEDBACK"}, headers=headers_b)
    assert listed_b_memory.json()["memories"] == []


# --- M/N: existing (non-AI) flows unaffected -------------------------------

async def test_normal_owner_explicit_memory_still_works(client) -> None:
    token, _tenant_id = await _register(client, "Phase21 Normal Memory Co", "phase21-normal-memory@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    created = await client.post(
        "/api/v1/memory",
        json={"memory_type": "OWNER_PREFERENCE", "key": "brand_tone", "value": "friendly", "source": "OWNER_EXPLICIT"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "ACTIVE"
    assert created.json()["memory_type"] == "OWNER_PREFERENCE"


async def test_normal_human_approval_still_works(client) -> None:
    from app.db.session import async_session_maker

    token, tenant_id = await _register(client, "Phase21 Normal Approval Co", "phase21-normal-approval@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    exec_resp = await client.post(
        "/api/v1/tools/notifications.create_notification/execute",
        json={"input": {"title": "Needs approval", "body": "test"}},
        headers=headers,
    )
    # Default policy for notifications.create_notification is AUTO — a
    # human-requested approval scenario needs an explicit override, same
    # pattern as test_approval_flow.py.
    if exec_resp.status_code == 200 and "approval_request_id" not in exec_resp.json():
        from app.models.tool_policy import TenantToolPolicy
        from app.services.policy_service import ActionPolicy

        async with async_session_maker() as session:
            session.add(TenantToolPolicy(tenant_id=tenant_id, tool_name="notifications.create_notification", policy=ActionPolicy.APPROVAL_REQUIRED, enabled=True))
            await session.commit()
        exec_resp = await client.post(
            "/api/v1/tools/notifications.create_notification/execute",
            json={"input": {"title": "Needs approval", "body": "test"}},
            headers=headers,
        )
    assert exec_resp.status_code == 200, exec_resp.text
    approval_id = exec_resp.json()["approval_request_id"]

    approver_token = await _create_second_user(async_session_maker, tenant_id, role=Role.OWNER)
    approve_resp = await client.post(
        f"/api/v1/approvals/{approval_id}/approve", json={}, headers={"Authorization": f"Bearer {approver_token}"}
    )
    assert approve_resp.status_code == 200
    detail = await client.get(f"/api/v1/approvals/{approval_id}", headers={"Authorization": f"Bearer {approver_token}"})
    assert detail.json()["requested_by_type"] == "USER"  # not AI — the UI's badge condition correctly stays off
