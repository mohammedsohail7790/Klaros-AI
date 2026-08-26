import uuid

import pytest

from app.models.actor import ActorType
from app.models.rbac import Role
from app.tools.base import ExecutionContext
from app.tools.builtin.event_tools import GetEventInput

pytestmark = pytest.mark.asyncio


async def test_get_event_rejects_wrong_tenant(tool_registry, event_bus) -> None:
    owner_tenant = uuid.uuid4()
    attacker_tenant = uuid.uuid4()

    event = await event_bus.publish(
        tenant_id=owner_tenant, event_type="lead.created", source="test", payload={"secret": "x"}
    )

    attacker_context = ExecutionContext(
        tenant_id=attacker_tenant, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=Role.OWNER
    )

    with pytest.raises(Exception) as exc_info:
        await tool_registry.execute(
            "events.get_event", {"event_id": str(event.id)}, attacker_context
        )
    assert "not found" in str(exc_info.value).lower()

    owner_context = ExecutionContext(
        tenant_id=owner_tenant, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=Role.OWNER
    )
    output = await tool_registry.execute("events.get_event", {"event_id": str(event.id)}, owner_context)
    assert output.event_id == str(event.id)


async def test_missing_tenant_context_rejected(tool_registry) -> None:
    from app.tools.errors import ToolPermissionError

    no_tenant_context = ExecutionContext(
        tenant_id=None, actor_type=ActorType.AI, actor_id=None, role=None
    )
    with pytest.raises(ToolPermissionError):
        await tool_registry.execute(
            "notifications.create_notification",
            {"title": "x", "body": "y"},
            no_tenant_context,
        )
