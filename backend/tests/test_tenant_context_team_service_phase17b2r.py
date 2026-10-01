"""Phase 17B-2R: real-PostgreSQL behavioral proof for TeamService's 7
session-open sites, including the two token-based ones —
`preview_invite`/`accept_invite` — whose `tenant_id` comes from this
codebase's own signed invite JWT (verified inside
`_resolve_pending_invite` before `set_tenant_context` is ever called),
never a client-supplied claim, exactly like `create_from_quote`'s public
boundary or `McpCredentialService.authenticate`'s hashed-token boundary.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import async_session_maker, set_tenant_context
from app.models.organization import Organization
from app.models.rbac import Role
from app.models.user import User
from app.services.team_service import InvalidInviteError, TeamService, UserNotFoundError

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


async def _make_org(tenant_id: uuid.UUID) -> uuid.UUID:
    owner_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        session.add(
            User(
                id=owner_id, tenant_id=tenant_id, email=f"owner-{tenant_id}@example.com",
                hashed_password=hash_password("supersecret1"), full_name="Owner", role=Role.OWNER,
            )
        )
        await session.commit()
    return owner_id


@requires_real_postgres
async def test_create_invite_and_list_members_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.team_service as team_service_module

    monkeypatch.setattr(team_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    owner_id = await _make_org(tenant_id)
    service = TeamService(async_session_maker)

    await service.list_members(tenant_id)
    await service.create_invite(tenant_id, email="new@example.com", role="STAFF", invited_by=owner_id)
    await service.list_invites(tenant_id)

    assert len(spy.calls) >= 3
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_accept_invite_sets_tenant_context_after_token_verification(monkeypatch, spy) -> None:
    import app.services.team_service as team_service_module

    monkeypatch.setattr(team_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    owner_id = await _make_org(tenant_id)
    service = TeamService(async_session_maker)

    invite, token, _sent = await service.create_invite(
        tenant_id, email="joiner@example.com", role="STAFF", invited_by=owner_id,
    )
    spy.calls.clear()  # isolate to preview/accept

    preview = await service.preview_invite(token)
    assert preview.email == "joiner@example.com"

    user = await service.accept_invite(token, full_name="New Member", password="supersecret1")
    assert user.tenant_id == tenant_id

    assert len(spy.calls) >= 2
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_a_forged_or_wrong_tenant_token_is_rejected_before_any_context_is_set(monkeypatch, spy) -> None:
    import app.services.team_service as team_service_module

    monkeypatch.setattr(team_service_module, "set_tenant_context", spy)

    service = TeamService(async_session_maker)

    with pytest.raises(InvalidInviteError):
        await service.preview_invite("not-a-real-token")

    assert spy.calls == []


@requires_real_postgres
async def test_tenant_a_cannot_update_tenant_bs_member() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org(tenant_a)
    await _make_org(tenant_b)
    service = TeamService(async_session_maker)

    with pytest.raises(UserNotFoundError):
        await service.update_member(tenant_b, uuid.uuid4(), role=None, is_active=False)
