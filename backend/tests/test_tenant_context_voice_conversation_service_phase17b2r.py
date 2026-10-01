"""Phase 17B-2R: real-PostgreSQL behavioral proof that
VoiceConversationService's own, independently-opened session (1 site,
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
from app.services.knowledge_qa_service import KnowledgeQAService
from app.services.knowledge_retrieval_service import KnowledgeRetrievalService
from app.services.knowledge_service import KnowledgeService
from app.services.voice_call_service import VoiceCallService
from app.services.voice_conversation_service import VoiceConversationService

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


class _DisconnectedProvider:
    is_connected = False
    name = "none"
    model = "none"

    async def generate_structured(self, prompt: str):
        raise AssertionError("must never be called when is_connected is False")


async def _make_org_and_customer(tenant_id: uuid.UUID, phone: str) -> None:
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        session.add(
            Customer(
                tenant_id=tenant_id, name="Voice Test Customer",
                email="voicecaller@example.com", phone=phone,
                phone_normalized=normalize_phone(phone),
            )
        )
        await session.commit()


def _service(fake_provider, tool_registry) -> VoiceConversationService:
    call_service = VoiceCallService(async_session_maker)
    ai_execution = AIExecutionService(tool_registry)
    knowledge_service = KnowledgeService(async_session_maker)
    retrieval_service = KnowledgeRetrievalService(async_session_maker, knowledge_service)
    qa_service = KnowledgeQAService(async_session_maker, retrieval_service, fake_provider)
    return VoiceConversationService(async_session_maker, call_service, ai_execution, fake_provider, qa_service)


@requires_real_postgres
async def test_identify_caller_sets_tenant_context(monkeypatch, spy, tool_registry) -> None:
    import app.services.voice_conversation_service as vcs_module

    monkeypatch.setattr(vcs_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    phone = f"+1555{uuid.uuid4().int % 10_000_000:07d}"
    await _make_org_and_customer(tenant_id, phone)
    service = _service(_DisconnectedProvider(), tool_registry)

    customer = await service._identify_caller(tenant_id, phone)
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

    service = _service(_DisconnectedProvider(), tool_registry)
    customer = await service._identify_caller(tenant_b, phone)
    assert customer is None
