"""Morning Brief scheduling: driven by the Event Worker's tick loop (see
EventWorker(on_tick=...) in app/events/worker.py), not a second
sleep-based scheduler. Idempotent per tenant-local calendar day."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.ai.execution_service import AIExecutionService
from app.db.session import async_session_maker
from app.models.morning_brief import MorningBrief
from app.models.organization import Organization
from app.services.morning_brief_service import MorningBriefService

pytestmark = pytest.mark.asyncio


async def _make_org(*, enabled: bool, local_time: str, timezone_name: str = "UTC") -> uuid.UUID:
    async with async_session_maker() as session:
        org = Organization(
            name="Scheduled Brief Co",
            slug=f"scheduled-brief-{uuid.uuid4().hex[:8]}",
            morning_brief_enabled=enabled,
            morning_brief_local_time=local_time,
            morning_brief_timezone=timezone_name,
        )
        session.add(org)
        await session.commit()
        await session.refresh(org)
        return org.id


async def test_scheduler_generates_a_brief_once_its_local_time_has_passed(tool_registry) -> None:
    now_utc = datetime.now(timezone.utc)
    past_time = (now_utc - timedelta(minutes=5)).strftime("%H:%M")
    org_id = await _make_org(enabled=True, local_time=past_time)

    service = MorningBriefService(async_session_maker, AIExecutionService(tool_registry))
    await service.check_and_generate_scheduled()

    async with async_session_maker() as session:
        briefs = (
            await session.execute(select(MorningBrief).where(MorningBrief.tenant_id == org_id))
        ).scalars().all()
    assert len(briefs) == 1
    assert briefs[0].generated_by == "SYSTEM"


async def test_scheduler_is_idempotent_within_the_same_local_day(tool_registry) -> None:
    now_utc = datetime.now(timezone.utc)
    past_time = (now_utc - timedelta(minutes=5)).strftime("%H:%M")
    org_id = await _make_org(enabled=True, local_time=past_time)

    service = MorningBriefService(async_session_maker, AIExecutionService(tool_registry))
    await service.check_and_generate_scheduled()
    await service.check_and_generate_scheduled()
    await service.check_and_generate_scheduled()

    async with async_session_maker() as session:
        briefs = (
            await session.execute(select(MorningBrief).where(MorningBrief.tenant_id == org_id))
        ).scalars().all()
    assert len(briefs) == 1


async def test_scheduler_skips_disabled_organizations(tool_registry) -> None:
    now_utc = datetime.now(timezone.utc)
    past_time = (now_utc - timedelta(minutes=5)).strftime("%H:%M")
    org_id = await _make_org(enabled=False, local_time=past_time)

    service = MorningBriefService(async_session_maker, AIExecutionService(tool_registry))
    await service.check_and_generate_scheduled()

    async with async_session_maker() as session:
        briefs = (
            await session.execute(select(MorningBrief).where(MorningBrief.tenant_id == org_id))
        ).scalars().all()
    assert briefs == []


async def test_scheduler_skips_organizations_whose_local_time_has_not_arrived_yet(tool_registry) -> None:
    now_utc = datetime.now(timezone.utc)
    future_time = (now_utc + timedelta(hours=2)).strftime("%H:%M")
    org_id = await _make_org(enabled=True, local_time=future_time)

    service = MorningBriefService(async_session_maker, AIExecutionService(tool_registry))
    await service.check_and_generate_scheduled()

    async with async_session_maker() as session:
        briefs = (
            await session.execute(select(MorningBrief).where(MorningBrief.tenant_id == org_id))
        ).scalars().all()
    assert briefs == []
