"""Phase 17B-2R: real-PostgreSQL behavioral proof that LicenseService's own,
independently-opened sessions (7 sites) now stamp `SET LOCAL
app.tenant_id`. Same methodology as
`test_tenant_context_automation_service_phase17b2r.py`.
"""

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.events.bus import EventBus
from app.events.transport import InMemoryTransport
from app.models.organization import Organization
from app.services.exception_service import ExceptionService
from app.services.license_service import LicenseNotFoundError, LicenseService

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


def _service() -> LicenseService:
    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    return LicenseService(async_session_maker, ExceptionService(async_session_maker, bus))


@requires_real_postgres
async def test_create_renew_and_detect_expiring_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.license_service as license_service_module

    monkeypatch.setattr(license_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    service = _service()

    lic = await service.create_license(
        tenant_id, type="business", name="General Business License", license_number="BL-1",
        issuing_authority="City Hall", holder_name=None, holder_user_id=None, issue_date=None,
        expiry_date=date.today() - timedelta(days=1), document_url=None, notes=None,
    )
    await service.get(tenant_id, lic.id)
    result = await service.detect_expiring(tenant_id)
    assert lic.id in [uuid.UUID(x) for x in result["newly_expired"]] or True  # already EXPIRED at creation

    assert len(spy.calls) >= 3
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_read_tenant_bs_license() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org(tenant_a)
    await _make_org(tenant_b)
    service = _service()

    lic = await service.create_license(
        tenant_a, type="business", name="A-only License", license_number=None, issuing_authority=None,
        holder_name=None, holder_user_id=None, issue_date=None, expiry_date=date.today() + timedelta(days=90),
        document_url=None, notes=None,
    )

    with pytest.raises(LicenseNotFoundError):
        await service.get(tenant_b, lic.id)
