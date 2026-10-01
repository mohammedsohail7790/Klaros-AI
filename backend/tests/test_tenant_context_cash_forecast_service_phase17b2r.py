"""Phase 17B-2R: real-PostgreSQL behavioral proof that CashForecastService's
own, independently-opened sessions (2 sites) now stamp `SET LOCAL
app.tenant_id`.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.organization import Organization
from app.services.cash_forecast_service import CashForecastNotFoundError, CashForecastService

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
async def test_generate_and_weekly_projection_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.cash_forecast_service as cfs_module

    monkeypatch.setattr(cfs_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    service = CashForecastService(async_session_maker)

    forecast = await service.generate(tenant_id)
    await service.weekly_projection(tenant_id, forecast.id)

    assert len(spy.calls) >= 2
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_forecast_isolated_from_tenant_b() -> None:
    """Phase 17B-4: CashForecastService.weekly_projection previously resolved
    the CashForecast row via a bare `session.get(CashForecast, forecast_id)`
    with no tenant_id check, so tenant_b could read tenant_a's
    starting_cash/starting_cash_source/generated_at/tenant_id by supplying
    tenant_a's forecast_id — even though the forecast's line ITEMS were
    already correctly tenant-filtered. Fixed: the forecast lookup itself is
    now tenant-scoped and raises CashForecastNotFoundError for a
    cross-tenant (or nonexistent) forecast_id, exactly like "not found" —
    no cross-tenant existence/metadata is disclosed.
    """
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org(tenant_a)
    await _make_org(tenant_b)
    service = CashForecastService(async_session_maker)

    forecast_a = await service.generate(tenant_a)

    with pytest.raises(CashForecastNotFoundError):
        await service.weekly_projection(tenant_b, forecast_a.id)

    # Sanity: the rightful owner can still read its own forecast fine.
    rows_a = await service.weekly_projection(tenant_a, forecast_a.id)
    assert isinstance(rows_a, list) and len(rows_a) == 13


@requires_real_postgres
async def test_weekly_projection_unknown_forecast_id_not_found() -> None:
    tenant_a = uuid.uuid4()
    await _make_org(tenant_a)
    service = CashForecastService(async_session_maker)

    with pytest.raises(CashForecastNotFoundError):
        await service.weekly_projection(tenant_a, uuid.uuid4())
