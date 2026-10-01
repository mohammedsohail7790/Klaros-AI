"""Phase 17B-2R: real-PostgreSQL behavioral proof that
OpenAIRealtimeVoiceBridge's own, independently-opened session (1 site,
`_identify_caller`) now stamps `SET LOCAL app.tenant_id`.
"""

import uuid

import pytest
from sqlalchemy import text

from app.ai.execution_service import AIExecutionService
from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.crm import Customer
from app.models.organization import Organization
from app.services.customer_matching import normalize_phone
from app.services.openai_realtime_voice_service import OpenAIRealtimeVoiceBridge
from app.services.voice_call_service import VoiceCallService

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)


class _ContextSpy:
    def __init__(self):
        self.calls: list[tuple[uuid.UUID | None, str | None]] = []

    async def __call__(self, session, tenant_id):
        await set_tenant_context(session, tenant_id)
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            readback = await session.scalar(text("select current_setting('app.tenant_id', true)"))
        else:
            readback = None
        self.calls.append((tenant_id, readback))


@pytest.fixture
def spy():
    return _ContextSpy()


async def _make_org_and_customer(tenant_id: uuid.UUID, phone: str) -> None:
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        session.add(
            Customer(
                tenant_id=tenant_id, name="Realtime Voice Test Customer",
                email="realtimecaller@example.com", phone=phone,
                phone_normalized=normalize_phone(phone),
            )
        )
        await session.commit()


def _bridge(tool_registry) -> OpenAIRealtimeVoiceBridge:
    return OpenAIRealtimeVoiceBridge(
        tool_registry, AIExecutionService(tool_registry), async_session_maker, VoiceCallService(async_session_maker),
    )


@requires_real_postgres
async def test_identify_caller_sets_tenant_context(monkeypatch, spy, tool_registry) -> None:
    import app.services.openai_realtime_voice_service as orvs_module

    monkeypatch.setattr(orvs_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    phone = f"+1555{uuid.uuid4().int % 10_000_000:07d}"
    await _make_org_and_customer(tenant_id, phone)
    bridge = _bridge(tool_registry)

    customer = await bridge._identify_caller(tenant_id, phone)
    assert customer is not None
    assert customer.tenant_id == tenant_id

    assert len(spy.calls) >= 1
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_caller_never_identified_for_tenant_b(tool_registry) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    phone = f"+1555{uuid.uuid4().int % 10_000_000:07d}"
    await _make_org_and_customer(tenant_a, phone)
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_b, name=f"org-{tenant_b}", slug=f"org-{tenant_b}"))
        await session.commit()

    bridge = _bridge(tool_registry)
    customer = await bridge._identify_caller(tenant_b, phone)
    assert customer is None
