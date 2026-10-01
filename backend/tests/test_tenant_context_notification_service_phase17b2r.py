"""Phase 17B-2R: real-PostgreSQL behavioral proof that NotificationService's
own, independently-opened sessions (`self._session_factory()`, 10 sites)
now stamp `SET LOCAL app.tenant_id`. Same methodology as
`test_tenant_context_automation_service_phase17b2r.py` — see that file's
header for the full rationale.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.notification import NotificationType
from app.models.organization import Organization
from app.services.notification_service import NotificationService

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
async def test_notify_list_and_mark_read_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.notification_service as notification_service_module

    monkeypatch.setattr(notification_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    service = NotificationService(async_session_maker)

    notification = await service.notify(
        tenant_id, NotificationType.NEW_LEAD, title="New lead", body="A new lead arrived",
    )
    assert notification is not None

    rows = await service.list_for_tenant(tenant_id)
    assert len(rows) == 1

    count = await service.unread_count(tenant_id)
    assert count == 1

    read = await service.mark_read(tenant_id, notification.id)
    assert read.read_at is not None

    assert len(spy.calls) >= 4
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_read_or_mark_tenant_bs_notification() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org(tenant_a)
    await _make_org(tenant_b)
    service = NotificationService(async_session_maker)

    notification = await service.notify(
        tenant_a, NotificationType.NEW_LEAD, title="A-only", body="only tenant A should see this",
    )
    assert notification is not None

    tenant_b_rows = await service.list_for_tenant(tenant_b)
    assert notification.id not in [n.id for n in tenant_b_rows]

    with pytest.raises(ValueError):
        await service.mark_read(tenant_b, notification.id)

    with pytest.raises(ValueError):
        await service.dismiss(tenant_b, notification.id)


@requires_real_postgres
async def test_preferences_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.notification_service as notification_service_module

    monkeypatch.setattr(notification_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    service = NotificationService(async_session_maker)
    user_id = uuid.uuid4()

    await service.set_preference(
        tenant_id, user_id, NotificationType.NEW_LEAD, "EMAIL", True,
    )
    prefs = await service.get_preferences(tenant_id, user_id)
    assert any(p["type"] == NotificationType.NEW_LEAD.value and p["channel"] == "EMAIL" and p["enabled"] for p in prefs)

    assert len(spy.calls) >= 1
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_pool_reuse_a_then_b_never_leaks_context() -> None:
    """§17 pool-safety proof, applied to NotificationService specifically:
    tenant A's notify(), then tenant B's notify(), on a small pool, must
    never let B observe A's GUC (or vice versa)."""
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org(tenant_a)
    await _make_org(tenant_b)
    service = NotificationService(async_session_maker)

    for _ in range(3):
        await service.notify(tenant_a, NotificationType.NEW_LEAD, title="a", body="a")
        async with async_session_maker() as session:
            readback = await session.scalar(text("select current_setting('app.tenant_id', true)"))
            # A fresh session from the pool must not silently inherit A's
            # SET LOCAL — SET LOCAL resets at transaction/connection-return
            # boundary, so a brand-new session's own (unset) transaction
            # should read back NULL/empty, not tenant_a's id.
            assert readback in (None, "")

        await service.notify(tenant_b, NotificationType.NEW_LEAD, title="b", body="b")
        async with async_session_maker() as session:
            readback = await session.scalar(text("select current_setting('app.tenant_id', true)"))
            assert readback in (None, "")
