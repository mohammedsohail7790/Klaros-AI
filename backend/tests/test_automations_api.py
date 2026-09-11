"""app/api/v1/automations.py — the authenticated REST API, exercised over
real HTTP (httpx ASGITransport), matching every other domain's own
*_api.py test convention (see tests/test_crm_api.py)."""

import uuid

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


async def test_create_publish_and_manually_trigger_over_http(client: AsyncClient) -> None:
    token = await _register(client, "Automation API Co", "owner@automationapi.com")
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/api/v1/automations",
        json={
            "name": "Welcome Notify", "trigger_type": "MANUAL", "trigger_config": {}, "condition": None,
            "steps": [{"action": "notifications.create_notification", "params": {"title": "hi", "body": "hello"}}],
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    automation_id = created.json()["id"]
    assert created.json()["status"] == "DRAFT"

    published = await client.post(f"/api/v1/automations/{automation_id}/publish", headers=headers)
    assert published.status_code == 200
    assert published.json()["status"] == "ENABLED"

    triggered = await client.post(f"/api/v1/automations/{automation_id}/trigger", json={"context": {}}, headers=headers)
    assert triggered.status_code == 200, triggered.text
    execution = triggered.json()
    assert execution["status"] == "COMPLETED"

    executions = await client.get(f"/api/v1/automations/{automation_id}/executions", headers=headers)
    assert executions.status_code == 200
    assert len(executions.json()["executions"]) == 1

    detail = await client.get(f"/api/v1/automations/executions/{execution['id']}", headers=headers)
    assert detail.status_code == 200
    assert len(detail.json()["steps"]) == 1
    assert detail.json()["steps"][0]["status"] == "SUCCEEDED"


async def test_manual_trigger_with_a_real_entity_id_over_http(client: AsyncClient) -> None:
    """Regression: `entity_id`, once supplied, must reach the real
    AutomationExecution.entity_id UUID column correctly — a prior version
    of this endpoint passed the raw string straight through, which no
    existing test caught because none supplied an entity_id at all."""
    token = await _register(client, "Automation Entity Id Co", "owner@automationentityid.com")
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/api/v1/automations",
        json={
            "name": "Entity Id Trigger", "trigger_type": "MANUAL", "trigger_config": {}, "condition": None,
            "steps": [{"action": "notifications.create_notification", "params": {"title": "hi", "body": "hello"}}],
        },
        headers=headers,
    )
    automation_id = created.json()["id"]
    await client.post(f"/api/v1/automations/{automation_id}/publish", headers=headers)

    entity_id = str(uuid.uuid4())
    triggered = await client.post(
        f"/api/v1/automations/{automation_id}/trigger",
        json={"entity_type": "quote", "entity_id": entity_id, "context": {}}, headers=headers,
    )
    assert triggered.status_code == 200, triggered.text
    assert triggered.json()["status"] == "COMPLETED"

    detail = await client.get(f"/api/v1/automations/executions/{triggered.json()['id']}", headers=headers)
    assert detail.json()["context"]["entity_type"] == "quote"
    assert detail.json()["context"]["entity_id"] == entity_id


async def test_manual_trigger_with_a_malformed_entity_id_is_rejected_over_http(client: AsyncClient) -> None:
    token = await _register(client, "Automation Bad Entity Id Co", "owner@automationbadentityid.com")
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/api/v1/automations",
        json={
            "name": "Bad Entity Id Trigger", "trigger_type": "MANUAL", "trigger_config": {}, "condition": None,
            "steps": [{"action": "notifications.create_notification", "params": {"title": "hi", "body": "hello"}}],
        },
        headers=headers,
    )
    automation_id = created.json()["id"]
    await client.post(f"/api/v1/automations/{automation_id}/publish", headers=headers)

    triggered = await client.post(
        f"/api/v1/automations/{automation_id}/trigger",
        json={"entity_type": "quote", "entity_id": "not-a-uuid", "context": {}}, headers=headers,
    )
    assert triggered.status_code == 422


async def test_schedule_trigger_create_publish_and_manual_tick_over_http(client: AsyncClient) -> None:
    token = await _register(client, "Schedule API Co", "owner@scheduleapi.com")
    headers = {"Authorization": f"Bearer {token}"}

    tz_default = await client.get("/api/v1/automations/timezone", headers=headers)
    assert tz_default.status_code == 200
    assert tz_default.json()["timezone"] == "UTC"

    tz_set = await client.put("/api/v1/automations/timezone", json={"timezone": "America/New_York"}, headers=headers)
    assert tz_set.status_code == 200
    assert tz_set.json()["timezone"] == "America/New_York"

    bad_tz = await client.put("/api/v1/automations/timezone", json={"timezone": "Not/Real"}, headers=headers)
    assert bad_tz.status_code == 422

    # Reset to UTC so the schedule dispatch below is deterministic regardless of wall-clock time.
    await client.put("/api/v1/automations/timezone", json={"timezone": "UTC"}, headers=headers)

    created = await client.post(
        "/api/v1/automations",
        json={
            "name": "HTTP Schedule", "trigger_type": "SCHEDULE", "trigger_config": {"frequency": "DAILY", "time": "00:00"},
            "condition": None,
            "steps": [{"action": "notifications.create_notification", "params": {"title": "scheduled", "body": "y"}}],
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    automation_id = created.json()["id"]

    await client.post(f"/api/v1/automations/{automation_id}/publish", headers=headers)

    detail = await client.get(f"/api/v1/automations/{automation_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["next_scheduled_run"] is not None

    tick = await client.post("/api/v1/automations/scheduled/dispatch-tick", headers=headers)
    assert tick.status_code == 200
    # 00:00 is always <= "now" in UTC on any given day, so this always dispatches.
    assert len(tick.json()["dispatched_execution_ids"]) == 1

    tick_again = await client.post("/api/v1/automations/scheduled/dispatch-tick", headers=headers)
    assert tick_again.json()["dispatched_execution_ids"] == []  # already fired for today


async def test_invalid_schedule_config_rejected_over_http(client: AsyncClient) -> None:
    token = await _register(client, "Bad Schedule Co", "owner@badschedule.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/automations",
        json={
            "name": "Bad", "trigger_type": "SCHEDULE", "trigger_config": {"frequency": "YEARLY", "time": "09:00"},
            "condition": None,
            "steps": [{"action": "notifications.create_notification", "params": {"title": "x", "body": "y"}}],
        },
        headers=headers,
    )
    assert resp.status_code == 422


async def test_dispatch_tick_is_tenant_isolated_over_http(client: AsyncClient) -> None:
    token_a = await _register(client, "Schedule Tenant A", "owner@scheduletenanta.com")
    token_b = await _register(client, "Schedule Tenant B", "owner@scheduletenantb.com")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    created = await client.post(
        "/api/v1/automations",
        json={
            "name": "A Schedule", "trigger_type": "SCHEDULE", "trigger_config": {"frequency": "DAILY", "time": "00:00"},
            "condition": None,
            "steps": [{"action": "notifications.create_notification", "params": {"title": "x", "body": "y"}}],
        },
        headers=headers_a,
    )
    automation_id = created.json()["id"]
    await client.post(f"/api/v1/automations/{automation_id}/publish", headers=headers_a)

    # Tenant B ticks its own schedule dispatch — must never touch tenant A's automation.
    tick_b = await client.post("/api/v1/automations/scheduled/dispatch-tick", headers=headers_b)
    assert tick_b.json()["dispatched_execution_ids"] == []

    executions_a = await client.get(f"/api/v1/automations/{automation_id}/executions", headers=headers_a)
    assert executions_a.json()["executions"] == []


async def test_read_only_role_cannot_set_timezone_over_http(client: AsyncClient) -> None:
    from sqlalchemy import select

    from app.core.security import create_access_token, hash_password
    from app.db.session import async_session_maker
    from app.models.rbac import Role
    from app.models.user import User

    token = await _register(client, "Timezone RBAC Co", "owner@timezonerbac.com")
    headers = {"Authorization": f"Bearer {token}"}

    async with async_session_maker() as session:
        owner = (await session.execute(select(User).where(User.email == "owner@timezonerbac.com"))).scalar_one()
        tenant_id = owner.tenant_id
        reader = User(
            tenant_id=tenant_id, email="reader@timezonerbac.com", full_name="Reader",
            hashed_password=hash_password("supersecret1"), role=Role.READ_ONLY,
        )
        session.add(reader)
        await session.commit()
        await session.refresh(reader)
        reader_token = create_access_token(reader.id, tenant_id, Role.READ_ONLY)

    reader_headers = {"Authorization": f"Bearer {reader_token}"}
    resp = await client.put("/api/v1/automations/timezone", json={"timezone": "UTC"}, headers=reader_headers)
    assert resp.status_code == 403


async def test_summary_reflects_real_counts_over_http(client: AsyncClient) -> None:
    token = await _register(client, "Automation Summary Co", "owner@automationsummary.com")
    headers = {"Authorization": f"Bearer {token}"}

    empty_summary = await client.get("/api/v1/automations/summary", headers=headers)
    assert empty_summary.status_code == 200
    assert empty_summary.json() == {
        "automations_total": 0, "automations_enabled": 0, "automations_scheduled": 0, "executions_running": 0,
        "executions_failed": 0, "executions_completed_today": 0, "pending_approvals": 0,
    }

    created = await client.post(
        "/api/v1/automations",
        json={
            "name": "Summary Test", "trigger_type": "MANUAL", "trigger_config": {}, "condition": None,
            "steps": [{"action": "notifications.create_notification", "params": {"title": "x", "body": "y"}}],
        },
        headers=headers,
    )
    automation_id = created.json()["id"]
    await client.post(f"/api/v1/automations/{automation_id}/publish", headers=headers)
    await client.post(f"/api/v1/automations/{automation_id}/trigger", json={"context": {}}, headers=headers)

    summary = await client.get("/api/v1/automations/summary", headers=headers)
    assert summary.json()["automations_total"] == 1
    assert summary.json()["automations_enabled"] == 1
    assert summary.json()["executions_completed_today"] == 1


async def test_forbidden_action_rejected_with_422_over_http(client: AsyncClient) -> None:
    token = await _register(client, "Automation Reject Co", "owner@automationreject.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/automations",
        json={
            "name": "Evil", "trigger_type": "MANUAL", "trigger_config": {}, "condition": None,
            "steps": [{"action": "crm.delete_customer", "params": {}}],
        },
        headers=headers,
    )
    assert resp.status_code == 422


async def test_malicious_condition_rejected_at_save_time_over_http(client: AsyncClient) -> None:
    token = await _register(client, "Automation Condition Co", "owner@automationcondition.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/automations",
        json={
            "name": "Bad Condition", "trigger_type": "MANUAL", "trigger_config": {}, "condition": {"eval": "1+1"},
            "steps": [{"action": "notifications.create_notification", "params": {"title": "x", "body": "y"}}],
        },
        headers=headers,
    )
    assert resp.status_code == 422


async def test_update_creates_new_version_visible_over_http(client: AsyncClient) -> None:
    token = await _register(client, "Automation Version Co", "owner@automationversion.com")
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/api/v1/automations",
        json={
            "name": "Versioned", "trigger_type": "MANUAL", "trigger_config": {}, "condition": None,
            "steps": [{"action": "notifications.create_notification", "params": {"title": "v1", "body": "y"}}],
        },
        headers=headers,
    )
    automation_id = created.json()["id"]

    updated = await client.put(
        f"/api/v1/automations/{automation_id}",
        json={
            "trigger_type": "MANUAL", "trigger_config": {}, "condition": None,
            "steps": [{"action": "notifications.create_notification", "params": {"title": "v2", "body": "y"}}],
        },
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["version_number"] == 2

    versions = await client.get(f"/api/v1/automations/{automation_id}/versions", headers=headers)
    assert [v["version_number"] for v in versions.json()["versions"]] == [1, 2]


async def test_enable_requires_publish_first_over_http(client: AsyncClient) -> None:
    token = await _register(client, "Automation Enable Co", "owner@automationenable.com")
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/api/v1/automations",
        json={
            "name": "Unpublished", "trigger_type": "MANUAL", "trigger_config": {}, "condition": None,
            "steps": [{"action": "notifications.create_notification", "params": {"title": "x", "body": "y"}}],
        },
        headers=headers,
    )
    automation_id = created.json()["id"]

    resp = await client.post(f"/api/v1/automations/{automation_id}/enabled", json={"enabled": True}, headers=headers)
    assert resp.status_code == 422


async def test_automations_are_tenant_isolated_over_http(client: AsyncClient) -> None:
    token_a = await _register(client, "Tenant A Automations", "owner@tenantaauto.com")
    token_b = await _register(client, "Tenant B Automations", "owner@tenantbauto.com")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    created = await client.post(
        "/api/v1/automations",
        json={
            "name": "A-only", "trigger_type": "MANUAL", "trigger_config": {}, "condition": None,
            "steps": [{"action": "notifications.create_notification", "params": {"title": "x", "body": "y"}}],
        },
        headers=headers_a,
    )
    automation_id = created.json()["id"]

    cross_tenant_get = await client.get(f"/api/v1/automations/{automation_id}", headers=headers_b)
    assert cross_tenant_get.status_code == 404

    listing_b = await client.get("/api/v1/automations", headers=headers_b)
    assert listing_b.json()["automations"] == []


async def test_read_only_role_cannot_create_automation_over_http(client: AsyncClient) -> None:
    """Rule 12/13: RBAC enforced at the API boundary — READ_ONLY has
    READ_AUTOMATIONS but not MANAGE_AUTOMATIONS."""
    import uuid

    from sqlalchemy import select

    from app.core.security import create_access_token, hash_password
    from app.db.session import async_session_maker
    from app.models.rbac import Role
    from app.models.user import User

    token = await _register(client, "Automation RBAC Co", "owner@automationrbac.com")
    headers = {"Authorization": f"Bearer {token}"}

    async with async_session_maker() as session:
        owner = (await session.execute(select(User).where(User.email == "owner@automationrbac.com"))).scalar_one()
        tenant_id = owner.tenant_id
        reader = User(
            tenant_id=tenant_id, email="reader@automationrbac.com", full_name="Reader",
            hashed_password=hash_password("supersecret1"), role=Role.READ_ONLY,
        )
        session.add(reader)
        await session.commit()
        await session.refresh(reader)
        reader_token = create_access_token(reader.id, tenant_id, Role.READ_ONLY)

    reader_headers = {"Authorization": f"Bearer {reader_token}"}
    resp = await client.post(
        "/api/v1/automations",
        json={
            "name": "Should Fail", "trigger_type": "MANUAL", "trigger_config": {}, "condition": None,
            "steps": [{"action": "notifications.create_notification", "params": {"title": "x", "body": "y"}}],
        },
        headers=reader_headers,
    )
    assert resp.status_code == 403
