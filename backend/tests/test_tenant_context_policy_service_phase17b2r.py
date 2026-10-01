"""Phase 17B-2R: real-PostgreSQL behavioral proof that PolicyService's own,
independently-opened sessions (5 sites) now stamp `SET LOCAL
app.tenant_id`. Same methodology as
`test_tenant_context_automation_service_phase17b2r.py`.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.organization import Organization
from app.services.policy_service import PolicyService
from app.tools.policy import ActionPolicy

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
async def test_set_policy_and_resolve_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.policy_service as policy_service_module

    monkeypatch.setattr(policy_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    service = PolicyService(async_session_maker)

    await service.set_policy(
        tenant_id, "crm.update_lead", ActionPolicy.APPROVAL_REQUIRED, actor_id=None,
    )
    resolved = await service.resolve(tenant_id, "crm.update_lead")
    assert resolved == ActionPolicy.APPROVAL_REQUIRED

    assert len(spy.calls) >= 2
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_policy_override_invisible_to_tenant_b() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org(tenant_a)
    await _make_org(tenant_b)
    service = PolicyService(async_session_maker)

    await service.set_policy(
        tenant_a, "crm.update_lead", ActionPolicy.APPROVAL_REQUIRED, actor_id=None,
    )

    # tenant_b never overrode this tool, so it must still resolve to the
    # static system default (AUTO), never tenant_a's APPROVAL_REQUIRED
    # override — proves the override itself is correctly tenant-isolated.
    resolved_b = await service.resolve(tenant_b, "crm.update_lead")
    assert resolved_b == ActionPolicy.AUTO
