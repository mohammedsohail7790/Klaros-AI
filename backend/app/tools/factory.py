from sqlalchemy.ext.asyncio import async_sessionmaker

from app.events.bus import EventBus
from app.tools.builtin.approval_tools import CreateApprovalRequest
from app.tools.builtin.audit_tools import RecordAction
from app.tools.builtin.event_tools import GetEvent, PublishEvent
from app.tools.builtin.notification_tools import CreateNotification
from app.tools.builtin.system_tools import GetCurrentTime, GetTenantContext
from app.tools.registry import ToolRegistry


def build_tool_registry(session_factory: async_sessionmaker, bus: EventBus) -> ToolRegistry:
    registry = ToolRegistry(session_factory)
    registry.register(GetTenantContext())
    registry.register(GetCurrentTime())
    registry.register(PublishEvent(bus))
    registry.register(GetEvent(session_factory))
    registry.register(CreateApprovalRequest(session_factory))
    registry.register(CreateNotification(session_factory))
    registry.register(RecordAction(session_factory))
    return registry
