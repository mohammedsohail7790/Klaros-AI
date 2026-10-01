"""Phase 17B-2R: real-PostgreSQL behavioral proof for `mcp_service.py`'s
two classes — `McpExposureService` (3 sites, all tenant-scoped) and
`McpCredentialService` (4 sites: 3 tenant-scoped, plus `authenticate`,
deliberately excluded — see below). Same methodology as
`test_tenant_context_automation_service_phase17b2r.py`.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.organization import Organization
from app.models.rbac import Role
from app.services.mcp_service import McpCredentialService, McpExposureService

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
async def test_exposure_service_sets_tenant_context(monkeypatch, spy) -> None:
    import app.services.mcp_service as mcp_service_module

    monkeypatch.setattr(mcp_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    service = McpExposureService(async_session_maker)

    await service.set_exposure(tenant_id, "crm.get_lead", True, created_by=None)
    await service.list_exposures(tenant_id)
    await service.list_enabled_tool_names(tenant_id)

    assert len(spy.calls) >= 3
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_credential_service_issue_list_revoke_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.mcp_service as mcp_service_module

    monkeypatch.setattr(mcp_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    service = McpCredentialService(async_session_maker)

    issued = await service.issue(tenant_id, "test cred", Role.OWNER, created_by=None)
    await service.list_credentials(tenant_id)
    await service.revoke(tenant_id, issued.id)

    assert len(spy.calls) >= 3
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_authenticate_never_sets_context_before_resolving_tenant(monkeypatch, spy) -> None:
    """`authenticate` IS the tenant-resolution step — no tenant_id exists
    to stamp before the token-hash lookup returns. Proves it stays that
    way: the spy is never called during an authenticate() call, whether
    the token is valid or not."""
    import app.services.mcp_service as mcp_service_module

    monkeypatch.setattr(mcp_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    cred_service = McpCredentialService(async_session_maker)
    issued = await cred_service.issue(tenant_id, "auth test", Role.OWNER, created_by=None)
    spy.calls.clear()

    resolved = await cred_service.authenticate(issued.raw_token)
    assert resolved is not None
    assert resolved.tenant_id == tenant_id
    assert spy.calls == []

    resolved_bad = await cred_service.authenticate("mcpkl_not-a-real-token")
    assert resolved_bad is None
    assert spy.calls == []


@requires_real_postgres
async def test_tenant_a_cannot_revoke_tenant_bs_credential() -> None:
    from app.services.mcp_service import McpCredentialError

    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org(tenant_a)
    await _make_org(tenant_b)
    service = McpCredentialService(async_session_maker)

    issued = await service.issue(tenant_a, "a-only", Role.OWNER, created_by=None)

    with pytest.raises(McpCredentialError):
        await service.revoke(tenant_b, issued.id)
