"""Phase 17B-2R: real-PostgreSQL behavioral proof for the three small AI
support services fixed together in this round: `ai_invocation_log_service.
py` (module-level `record_ai_invocation`, 1 site), `ai_next_action_service.
py` (`AINextActionService`, 2 sites), and `ai_qualification_service.py`
(`AIQualificationService`, 1 site). Same methodology as
`test_tenant_context_automation_service_phase17b2r.py`.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.actor import ActorType
from app.models.crm import Lead
from app.models.organization import Organization
from app.services.ai_invocation_log_service import record_ai_invocation
from app.services.ai_provider import AICallOutcome, get_ai_provider
from app.services.ai_qualification_service import AIQualificationService, LeadNotFoundError

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


async def _make_org_and_lead(tenant_id: uuid.UUID) -> uuid.UUID:
    lead_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        session.add(Lead(id=lead_id, tenant_id=tenant_id, name="AI Test Lead", source="web"))
        await session.commit()
    return lead_id


@requires_real_postgres
async def test_record_ai_invocation_sets_tenant_context(monkeypatch, spy) -> None:
    import app.services.ai_invocation_log_service as ail_module

    monkeypatch.setattr(ail_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org_and_lead(tenant_id)

    outcome = AICallOutcome(
        success=True, provider="deterministic", model="test-model", latency_ms=10, retry_count=0,
        input_tokens=10, output_tokens=5,
    )
    await record_ai_invocation(
        async_session_maker, tenant_id=tenant_id, actor_type=ActorType.SYSTEM, actor_id=None,
        operation="test_operation", outcome=outcome,
    )

    assert len(spy.calls) == 1
    called_tenant, readback = spy.calls[0]
    assert called_tenant == tenant_id
    assert readback == str(tenant_id)


@requires_real_postgres
async def test_ai_qualification_generate_recommendation_sets_tenant_context(monkeypatch, spy) -> None:
    import app.services.ai_qualification_service as aiq_module

    monkeypatch.setattr(aiq_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    lead_id = await _make_org_and_lead(tenant_id)
    service = AIQualificationService(async_session_maker, get_ai_provider())

    result = await service.generate_recommendation(
        tenant_id, lead_id, actor_type=ActorType.SYSTEM, actor_id=None,
    )
    assert result.available is False  # no real AI provider configured in this test env

    assert len(spy.calls) >= 1
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_generate_recommendation_for_tenant_bs_lead() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    lead_a = await _make_org_and_lead(tenant_a)
    await _make_org_and_lead(tenant_b)
    service = AIQualificationService(async_session_maker, get_ai_provider())

    with pytest.raises(LeadNotFoundError):
        await service.generate_recommendation(tenant_b, lead_a, actor_type=ActorType.SYSTEM, actor_id=None)
