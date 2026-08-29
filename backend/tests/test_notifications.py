"""Phase 10B: notification orchestration — NotificationService, its
event-driven creation, deduplication, tenant/recipient isolation, and the
tool/API layer."""

import uuid

import pytest

from app.models.actor import ActorType
from app.models.notification import NotificationPriority, NotificationType
from app.models.rbac import Role
from app.services.notification_service import NotificationService
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id, role=Role.OWNER, actor_id=None):
    return ExecutionContext(
        tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=actor_id or uuid.uuid4(), role=role
    )


async def test_notification_creation(tool_registry) -> None:
    service = NotificationService(tool_registry._session_factory)
    tenant_id = uuid.uuid4()
    n = await service.notify(
        tenant_id, NotificationType.NEW_LEAD, title="New lead", body="A new lead arrived.",
    )
    assert n is not None
    assert n.type == NotificationType.NEW_LEAD
    assert n.status == "SENT"


async def test_notification_deduplication(tool_registry) -> None:
    service = NotificationService(tool_registry._session_factory)
    tenant_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    first = await service.notify(
        tenant_id, NotificationType.APPROVAL_REQUIRED, title="Approval required", body="x",
        dedupe_key=f"approval.requested:{entity_id}",
    )
    second = await service.notify(
        tenant_id, NotificationType.APPROVAL_REQUIRED, title="Approval required", body="x",
        dedupe_key=f"approval.requested:{entity_id}",
    )
    assert first is not None
    assert second is None  # silently absorbed, not a second row

    rows = await service.list_for_tenant(tenant_id)
    assert len(rows) == 1


async def test_notification_without_dedupe_key_never_deduplicated(tool_registry) -> None:
    service = NotificationService(tool_registry._session_factory)
    tenant_id = uuid.uuid4()
    await service.notify(tenant_id, NotificationType.NEW_LEAD, title="Lead 1", body="x")
    await service.notify(tenant_id, NotificationType.NEW_LEAD, title="Lead 2", body="y")
    rows = await service.list_for_tenant(tenant_id)
    assert len(rows) == 2


async def test_tenant_isolation(tool_registry) -> None:
    service = NotificationService(tool_registry._session_factory)
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await service.notify(tenant_a, NotificationType.NEW_LEAD, title="A lead", body="x")

    rows_a = await service.list_for_tenant(tenant_a)
    rows_b = await service.list_for_tenant(tenant_b)
    assert len(rows_a) == 1
    assert len(rows_b) == 0


async def test_cannot_mark_read_across_tenants(tool_registry) -> None:
    service = NotificationService(tool_registry._session_factory)
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    n = await service.notify(tenant_a, NotificationType.NEW_LEAD, title="A lead", body="x")

    with pytest.raises(ValueError):
        await service.mark_read(tenant_b, n.id)


async def test_unread_count(tool_registry) -> None:
    service = NotificationService(tool_registry._session_factory)
    tenant_id = uuid.uuid4()
    n1 = await service.notify(tenant_id, NotificationType.NEW_LEAD, title="1", body="x")
    await service.notify(tenant_id, NotificationType.NEW_LEAD, title="2", body="y")

    assert await service.unread_count(tenant_id) == 2
    await service.mark_read(tenant_id, n1.id)
    assert await service.unread_count(tenant_id) == 1


async def test_mark_all_read(tool_registry) -> None:
    service = NotificationService(tool_registry._session_factory)
    tenant_id = uuid.uuid4()
    await service.notify(tenant_id, NotificationType.NEW_LEAD, title="1", body="x")
    await service.notify(tenant_id, NotificationType.NEW_LEAD, title="2", body="y")

    count = await service.mark_all_read(tenant_id)
    assert count == 2
    assert await service.unread_count(tenant_id) == 0


async def test_dismiss(tool_registry) -> None:
    service = NotificationService(tool_registry._session_factory)
    tenant_id = uuid.uuid4()
    n = await service.notify(tenant_id, NotificationType.NEW_LEAD, title="1", body="x")

    dismissed = await service.dismiss(tenant_id, n.id)
    assert dismissed.status == "DISMISSED"


async def test_critical_types_always_visible_in_app_even_if_disabled(tool_registry) -> None:
    service = NotificationService(tool_registry._session_factory)
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    with pytest.raises(ValueError):
        await service.set_preference(
            tenant_id, user_id, NotificationType.APPROVAL_REQUIRED, "IN_APP", False
        )


async def test_preferences_default_to_safe_values(tool_registry) -> None:
    service = NotificationService(tool_registry._session_factory)
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    prefs = await service.get_preferences(tenant_id, user_id)
    approval_email = next(
        p for p in prefs if p["type"] == NotificationType.APPROVAL_REQUIRED and p["channel"] == "EMAIL"
    )
    assert approval_email["enabled"] is True
    morning_brief_email = next(
        p for p in prefs if p["type"] == NotificationType.MORNING_BRIEF_READY and p["channel"] == "EMAIL"
    )
    assert morning_brief_email["enabled"] is False  # spec's example default


async def test_set_preference_persists(tool_registry) -> None:
    service = NotificationService(tool_registry._session_factory)
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    await service.set_preference(tenant_id, user_id, NotificationType.NEW_LEAD, "IN_APP", False)
    prefs = await service.get_preferences(tenant_id, user_id)
    row = next(p for p in prefs if p["type"] == NotificationType.NEW_LEAD and p["channel"] == "IN_APP")
    assert row["enabled"] is False


async def test_event_driven_approval_requested_creates_notification(tool_registry, event_bus) -> None:
    from app.models.event import EventType
    from app.tools.errors import ToolApprovalRequiredError
    from app.tools.policy import ActionPolicy, DEFAULT_TOOL_POLICIES

    DEFAULT_TOOL_POLICIES["notifications.create_notification"] = ActionPolicy.APPROVAL_REQUIRED
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    with pytest.raises(ToolApprovalRequiredError):
        await tool_registry.execute("notifications.create_notification", {"title": "x", "body": "y"}, ctx)
    await event_bus.process_pending(EventType.APPROVAL_REQUESTED)

    service = NotificationService(tool_registry._session_factory)
    rows = await service.list_for_tenant(tenant_id)
    assert any(r.type == NotificationType.APPROVAL_REQUIRED for r in rows)


async def test_event_driven_notification_is_deduplicated_on_replay(tool_registry, event_bus) -> None:
    """The same underlying business event delivered twice (e.g. a retried
    EventWorker tick after a transient failure) must never create two
    notifications — this is the real dedupe_key uniqueness constraint doing
    its job, not application-level counting."""
    from app.models.event import EventType
    from app.tools.errors import ToolApprovalRequiredError
    from app.tools.policy import ActionPolicy, DEFAULT_TOOL_POLICIES

    DEFAULT_TOOL_POLICIES["notifications.create_notification"] = ActionPolicy.APPROVAL_REQUIRED
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    with pytest.raises(ToolApprovalRequiredError) as exc_info:
        await tool_registry.execute("notifications.create_notification", {"title": "x", "body": "y"}, ctx)
    approval_id = exc_info.value.approval_request_id

    # Re-publish the identical approval.requested event a second time,
    # simulating a duplicate delivery — the handler must not fan out.
    await event_bus.publish(
        tenant_id=tenant_id,
        event_type=EventType.APPROVAL_REQUESTED,
        source="test_replay",
        entity_type="approval_request",
        entity_id=approval_id,
        payload={"tool_name": "notifications.create_notification"},
    )
    await event_bus.process_pending(EventType.APPROVAL_REQUESTED)

    service = NotificationService(tool_registry._session_factory)
    rows = await service.list_for_tenant(tenant_id)
    approval_rows = [r for r in rows if r.type == NotificationType.APPROVAL_REQUIRED and r.entity_id == approval_id]
    assert len(approval_rows) == 1


async def test_morning_brief_generates_one_grouped_notification(tool_registry, event_bus) -> None:
    from app.models.event import EventType

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer = await tool_registry.execute("crm.create_customer", {"name": "Notif Co"}, ctx)
    await tool_registry.execute(
        "retention.record_feedback", {"customer_id": customer.customer["id"], "rating": 1}, ctx
    )
    await tool_registry.execute("insights.generate_morning_brief", {}, ctx)
    await event_bus.process_pending(EventType.MORNING_BRIEF_GENERATED)

    service = NotificationService(tool_registry._session_factory)
    rows = await service.list_for_tenant(tenant_id)
    brief_notifications = [r for r in rows if r.type == NotificationType.MORNING_BRIEF_READY]
    assert len(brief_notifications) == 1


async def test_high_severity_exception_creates_notification(tool_registry, event_bus) -> None:
    from app.models.event import EventType

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer = await tool_registry.execute("crm.create_customer", {"name": "Exc Co"}, ctx)
    await tool_registry.execute(
        "retention.record_feedback",
        {"customer_id": customer.customer["id"], "rating": 1, "comment": "bad"},
        ctx,
    )
    await event_bus.process_pending(EventType.EXCEPTION_CREATED)
    service = NotificationService(tool_registry._session_factory)
    rows = await service.list_for_tenant(tenant_id)
    assert any(r.type == NotificationType.NEGATIVE_FEEDBACK for r in rows)


async def test_api_notification_endpoints(client, tool_registry) -> None:
    register = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Notif API Co",
            "full_name": "Owner",
            "email": "owner@notifapi.com",
            "password": "supersecret1",
        },
    )
    token = register.json()["tokens"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    service = NotificationService(tool_registry._session_factory)
    tenant_id = uuid.UUID(register.json()["user"]["tenant_id"])
    n = await service.notify(tenant_id, NotificationType.NEW_LEAD, title="Test", body="body")

    unread = await client.get("/api/v1/notifications/unread-count", headers=headers)
    assert unread.json()["unread_count"] == 1

    listed = await client.get("/api/v1/notifications", headers=headers)
    assert len(listed.json()["notifications"]) == 1

    read = await client.post(f"/api/v1/notifications/{n.id}/read", headers=headers)
    assert read.status_code == 200

    unread_after = await client.get("/api/v1/notifications/unread-count", headers=headers)
    assert unread_after.json()["unread_count"] == 0


async def test_api_notification_tenant_isolation(client, tool_registry) -> None:
    register_a = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Notif Tenant A",
            "full_name": "Owner",
            "email": "ownera@notiftenant.com",
            "password": "supersecret1",
        },
    )
    register_b = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Notif Tenant B",
            "full_name": "Owner",
            "email": "ownerb@notiftenant.com",
            "password": "supersecret1",
        },
    )
    token_a = register_a.json()["tokens"]["access_token"]
    tenant_b_id = uuid.UUID(register_b.json()["user"]["tenant_id"])

    service = NotificationService(tool_registry._session_factory)
    n = await service.notify(tenant_b_id, NotificationType.NEW_LEAD, title="B's lead", body="x")

    # Tenant A cannot see or mark read tenant B's notification.
    listed = await client.get("/api/v1/notifications", headers={"Authorization": f"Bearer {token_a}"})
    assert listed.json()["notifications"] == []

    read_attempt = await client.post(
        f"/api/v1/notifications/{n.id}/read", headers={"Authorization": f"Bearer {token_a}"}
    )
    assert read_attempt.status_code == 404
