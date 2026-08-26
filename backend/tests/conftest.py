import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("EVENT_TRANSPORT", "memory")

import asyncio  # noqa: E402
from collections.abc import AsyncGenerator  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.api.tool_deps import get_tool_registry, get_wired_event_bus  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import async_session_maker, engine  # noqa: E402
from app.events.bus import EventBus  # noqa: E402
from app.events.handlers import register_default_handlers  # noqa: E402
from app.events.transport import InMemoryTransport  # noqa: E402
from app.main import app  # noqa: E402
from app.tools.factory import build_tool_registry  # noqa: E402


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(autouse=True)
async def _reset_database() -> AsyncGenerator[None, None]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest_asyncio.fixture
async def event_bus() -> EventBus:
    """A fresh, isolated bus per test — in-memory transport, real Postgres
    (sqlite in tests) event store. Overrides the process-wide lru_cache
    singleton so tests never see another test's stream state.
    """
    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    register_default_handlers(bus)
    app.dependency_overrides[get_wired_event_bus] = lambda: bus
    yield bus
    app.dependency_overrides.pop(get_wired_event_bus, None)


@pytest_asyncio.fixture
async def tool_registry(event_bus: EventBus):
    registry = build_tool_registry(async_session_maker, event_bus)
    app.dependency_overrides[get_tool_registry] = lambda: registry
    yield registry
    app.dependency_overrides.pop(get_tool_registry, None)


@pytest_asyncio.fixture
async def client(event_bus: EventBus, tool_registry) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def _restore_tool_policies():
    """DEFAULT_TOOL_POLICIES is a shared module-level dict; tests that
    override a policy to exercise APPROVAL_REQUIRED/BLOCKED must not leak
    that override into other tests.
    """
    from app.tools.policy import DEFAULT_TOOL_POLICIES

    snapshot = dict(DEFAULT_TOOL_POLICIES)
    yield
    DEFAULT_TOOL_POLICIES.clear()
    DEFAULT_TOOL_POLICIES.update(snapshot)
