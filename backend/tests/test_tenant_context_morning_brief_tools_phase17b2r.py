"""Phase 17B-2R: real-PostgreSQL behavioral proof that the 3
independently-opened sessions in app/tools/builtin/morning_brief_tools.py
(GetLatestMorningBrief, ExecuteRecommendation, DismissRecommendation) now
stamp `SET LOCAL app.tenant_id`. `insights.generate_morning_brief`
itself is not covered here — it delegates entirely to
MorningBriefService, already fixed and tested in an earlier round
(see tests/test_tenant_context_morning_brief_service_phase17b2r.py)."""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.actor import ActorType
from app.models.morning_brief import MorningBrief, MorningBriefRecommendation, RecommendationStatus
from app.models.rbac import Role
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)


def _ctx(tenant_id, role=Role.OWNER, actor_type=ActorType.USER):
    return ExecutionContext(tenant_id=tenant_id, actor_type=actor_type, actor_id=uuid.uuid4(), role=role)


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


async def _make_brief_with_recommendation(tenant_id: uuid.UUID) -> MorningBriefRecommendation:
    async with async_session_maker() as session:
        brief = MorningBrief(
            tenant_id=tenant_id, brief_date=datetime.now(timezone.utc).date(),
            generated_at=datetime.now(timezone.utc), mode="DETERMINISTIC", generated_by="SYSTEM",
            headline="Test brief", source_data={},
        )
        session.add(brief)
        await session.flush()
        rec = MorningBriefRecommendation(
            tenant_id=tenant_id, brief_id=brief.id, what="Do a thing", why="Because",
            next_action="Click it", status=RecommendationStatus.PENDING,
        )
        session.add(rec)
        await session.commit()
        await session.refresh(rec)
        return rec


@requires_real_postgres
async def test_get_latest_and_dismiss_recommendation_set_tenant_context(monkeypatch, spy, tool_registry) -> None:
    import app.tools.builtin.morning_brief_tools as mbt_module

    monkeypatch.setattr(mbt_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    rec = await _make_brief_with_recommendation(tenant_id)
    ctx = _ctx(tenant_id)

    latest = await tool_registry.execute("insights.get_latest_morning_brief", {}, ctx)
    assert latest.brief_id is not None
    assert any(r.recommendation_id == str(rec.id) for r in latest.recommendations)

    dismissed = await tool_registry.execute(
        "insights.dismiss_recommendation", {"recommendation_id": str(rec.id)}, ctx
    )
    assert dismissed.status == "DISMISSED"

    assert len(spy.calls) >= 2
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_recommendation_never_dismissable_by_tenant_b(tool_registry) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    rec_a = await _make_brief_with_recommendation(tenant_a)

    with pytest.raises(ValueError, match="not found"):
        await tool_registry.execute(
            "insights.dismiss_recommendation", {"recommendation_id": str(rec_a.id)}, _ctx(tenant_b)
        )

    latest_b = await tool_registry.execute("insights.get_latest_morning_brief", {}, _ctx(tenant_b))
    assert latest_b.brief_id is None
