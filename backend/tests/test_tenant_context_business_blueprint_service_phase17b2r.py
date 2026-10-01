"""Phase 17B-2R: real-PostgreSQL behavioral proof that
BusinessBlueprintService's own, independently-opened sessions (12 sites)
now stamp `SET LOCAL app.tenant_id`. Same methodology as
`test_tenant_context_automation_service_phase17b2r.py`.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.business_blueprint import BlueprintSectionKey
from app.models.organization import Organization
from app.services.business_blueprint_service import BlueprintNotFoundError, BusinessBlueprintService

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


async def _make_org(tenant_id: uuid.UUID) -> None:
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        await session.commit()


@requires_real_postgres
async def test_get_or_create_draft_and_propose_claim_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.business_blueprint_service as bbs_module

    monkeypatch.setattr(bbs_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    service = BusinessBlueprintService(async_session_maker)

    blueprint = await service.get_or_create_draft(tenant_id, created_by=None)
    await service.propose_claim(
        tenant_id, blueprint.id, section_key=BlueprintSectionKey.IDENTITY.value, claim_type="fact",
        key="business_name", value="Acme Co", confidence=0.9, provenance="test", discovery_turn_id=None,
        evidence_ref=None,
    )
    await service.list_sections(tenant_id, blueprint.id)

    assert len(spy.calls) >= 3
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_read_tenant_bs_blueprint() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org(tenant_a)
    await _make_org(tenant_b)
    service = BusinessBlueprintService(async_session_maker)

    blueprint_a = await service.get_or_create_draft(tenant_a, created_by=None)

    with pytest.raises(BlueprintNotFoundError):
        await service.get_by_id(tenant_b, blueprint_a.id)
