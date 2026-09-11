import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("EVENT_TRANSPORT", "memory")
# Real retrieval/ranking logic, exercised without an OpenAI API key — see
# app/services/embedding_provider.py's DeterministicEmbeddingProvider
# docstring for why this is explicit opt-in, never production's default.
os.environ.setdefault("EMBEDDING_PROVIDER", "deterministic")
# Same reasoning, for the AI Voice Receptionist's speech providers — see
# app/services/speech_provider.py.
os.environ.setdefault("STT_PROVIDER", "deterministic")
os.environ.setdefault("TTS_PROVIDER", "deterministic")
os.environ.setdefault("STORAGE_LOCAL_ROOT", "/tmp/klaros-test-storage")
# Test-only key, never a real secret — exercises the properly-configured
# path for app/integrations/credential_store.py's encryption boundary, so
# the test suite genuinely proves tenant OAuth/API-key credentials get
# encrypted with a real configured key, not silently falling through to
# the insecure hardcoded default (see GET /ready's credential_encryption
# check in app/main.py, first-customer production gate).
os.environ.setdefault("INTEGRATION_CREDENTIAL_ENCRYPTION_KEY", "test-only-not-a-real-secret-encryption-key")

from collections.abc import AsyncGenerator  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.api.tool_deps import get_tool_registry, get_wired_event_bus  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import async_session_maker, engine  # noqa: E402
from app.events.bus import EventBus  # noqa: E402
from app.events.crm_handlers import register_crm_handlers  # noqa: E402
from app.events.finance_handlers import register_finance_handlers  # noqa: E402
from app.events.handlers import register_default_handlers  # noqa: E402
from app.events.marketing_handlers import register_marketing_handlers  # noqa: E402
from app.events.retention_handlers import register_retention_handlers  # noqa: E402
from app.events.operations_handlers import register_operations_handlers  # noqa: E402
from app.events.notification_handlers import register_notification_handlers  # noqa: E402
from app.events.transport import InMemoryTransport  # noqa: E402
from app.events.automation_handlers import register_automation_handlers  # noqa: E402
from app.main import app  # noqa: E402
from app.core.rate_limit import reset_rate_limit_backend  # noqa: E402
from app.tools.factory import build_tool_registry  # noqa: E402


def _current_alembic_head() -> str:
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    alembic_ini = Path(__file__).resolve().parent.parent / "alembic.ini"
    config = Config(str(alembic_ini))
    return ScriptDirectory.from_config(config).get_current_head()


@pytest_asyncio.fixture(autouse=True)
async def _reset_database() -> AsyncGenerator[None, None]:
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        # This schema is built directly from the current models, not from
        # running real Alembic migrations — but that means it genuinely DOES
        # reflect the current code's expected head, so stamp `alembic_version`
        # to say so. Without this, GET /ready's Phase 27 migration-head check
        # (app/main.py) would see a missing `alembic_version` table and report
        # `not_ready` for every single test, on both SQLite and real Postgres.
        await conn.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
        )
        await conn.execute(text("DELETE FROM alembic_version"))
        await conn.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:v)"), {"v": _current_alembic_head()}
        )
    reset_rate_limit_backend()
    yield


@pytest_asyncio.fixture
async def event_bus() -> EventBus:
    """A fresh, isolated bus per test — in-memory transport, real Postgres
    (sqlite in tests) event store. Overrides the process-wide lru_cache
    singleton so tests never see another test's stream state.
    """
    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    register_default_handlers(bus)
    register_crm_handlers(bus, async_session_maker)
    register_operations_handlers(bus, async_session_maker)
    register_finance_handlers(bus, async_session_maker)
    register_marketing_handlers(bus, async_session_maker)
    register_retention_handlers(bus, async_session_maker)
    register_notification_handlers(bus, async_session_maker)
    # A dedicated registry for the automation dispatcher only — avoids a
    # circular fixture dependency with `tool_registry` (which itself needs
    # this same `event_bus`), while still exercising the real governed
    # tool-execution path for every automation action.
    from app.ai.execution_service import AIExecutionService

    _automation_registry = build_tool_registry(async_session_maker, bus)
    register_automation_handlers(bus, async_session_maker, AIExecutionService(_automation_registry))
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
