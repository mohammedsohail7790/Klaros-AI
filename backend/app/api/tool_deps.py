from functools import lru_cache

from app.db.session import async_session_maker
from app.events.bus import EventBus
from app.events.factory import get_event_bus
from app.events.handlers import register_default_handlers
from app.tools.factory import build_tool_registry
from app.tools.registry import ToolRegistry


@lru_cache
def get_tool_registry() -> ToolRegistry:
    return build_tool_registry(async_session_maker, get_event_bus())


@lru_cache
def get_wired_event_bus() -> EventBus:
    bus = get_event_bus()
    register_default_handlers(bus)
    return bus
