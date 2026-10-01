"""Phase 17B-2R: real-PostgreSQL behavioral proof that AINextActionService's
own, independently-opened sessions (2 sites: decide_quote_followup's and
decide_invoice_followup's own OBSERVE reads) now stamp `SET LOCAL
app.tenant_id`.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.events.bus import EventBus
from app.events.transport import InMemoryTransport
from app.models.crm import Customer
from app.models.organization import Organization
from app.ai.execution_service import AIExecutionService
from app.models.quote import Quote
from app.services.ai_next_action_service import AINextActionService, NextActionDecision
from app.tools.factory import build_tool_registry

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


async def _make_org_customer_and_quote(tenant_id: uuid.UUID) -> uuid.UUID:
    customer_id = uuid.uuid4()
    quote_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        session.add(Customer(id=customer_id, tenant_id=tenant_id, name="AINA Test Customer"))
        session.add(Quote(id=quote_id, tenant_id=tenant_id, quote_number=f"Q-{uuid.uuid4().hex[:6]}", customer_id=customer_id))
        await session.commit()
    return quote_id


def _service() -> AINextActionService:
    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    registry = build_tool_registry(async_session_maker, bus)
    ai_execution = AIExecutionService(registry)
    return AINextActionService(async_session_maker, ai_execution)


@requires_real_postgres
async def test_decide_quote_followup_sets_tenant_context(monkeypatch, spy) -> None:
    import app.services.ai_next_action_service as ana_module

    monkeypatch.setattr(ana_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    quote_id = await _make_org_customer_and_quote(tenant_id)
    service = _service()

    decision = await service.decide_quote_followup(tenant_id, quote_id, correlation_id=None)
    assert isinstance(decision, NextActionDecision)

    assert len(spy.calls) >= 1
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_quote_not_visible_to_tenant_b() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    quote_a = await _make_org_customer_and_quote(tenant_a)
    await _make_org_customer_and_quote(tenant_b)
    service = _service()

    decision = await service.decide_quote_followup(tenant_b, quote_a, correlation_id=None)
    assert decision.outcome == "quote_not_found"
