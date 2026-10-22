"""Clinic outreach Phase 4 end to end: a campaign that passed the shadow checks goes live, the hourly Outreach Runner drafts and sends through a FAKE channel on a simulated clock,
a clinic asking about terms gets one answer, a clinic opting out gets one confirmation and is never written to again, and every safety stop holds. No real email provider is ever used."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.api.tool_deps import get_tool_registry
from app.db.session import async_session_maker
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.clinic_outreach import ClinicOutreach, ClinicOutreachMessage, OutreachStage
from app.models.marketing import Campaign, CampaignStatus
from app.models.organization import Organization
from app.models.rbac import Role
from app.services import clinic_outreach_engine, clinic_outreach_sender
from app.tools.base import ExecutionContext
from tests.test_clinic_outreach_phase1_end_to_end import SEND_PATHS, _resolve
from tests.test_clinic_outreach_sender import FakeChannel
from tests.test_clinic_outreach_service import USER, _svc
from tests.test_outreach_golive import _ready_for_golive

pytestmark = pytest.mark.asyncio


class _Clock:
    def __init__(self, now):
        self.now = now


def _freeze(monkeypatch, clock):
    real = datetime

    class FakeDatetime(real):
        @classmethod
        def now(cls, tz=None):
            return clock.now if tz is None else clock.now.astimezone(tz)

    monkeypatch.setattr(clinic_outreach_engine, "datetime", FakeDatetime)
    monkeypatch.setattr(clinic_outreach_sender, "datetime", FakeDatetime)


def _utc(d):
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)


async def _run_hours(tid, ctx, clock, start, days, hours=(0, 3, 6, 7, 8, 9, 10, 11, 12, 13, 15, 18, 21)):
    for day in range(days):
        for hour in hours:
            clock.now = start + timedelta(days=day, hours=hour, minutes=5)
            await get_tool_registry().execute("outreach.run_engine", {}, ctx)


async def test_phase_4_live_flow_sends_paces_answers_and_stops_safely(monkeypatch) -> None:
    for module, dotted in SEND_PATHS:  # the real channels must never be reached: the fake channel replaces get_clinic_channel
        target, attr = _resolve(module, dotted)
        monkeypatch.setattr(target, attr, lambda *a, _n=f"{module}.{dotted}", **k: (_ for _ in ()).throw(AssertionError(f"real send path called: {_n}")))
    channel = FakeChannel()
    monkeypatch.setattr(clinic_outreach_sender, "get_clinic_channel", lambda sf: channel)
    monkeypatch.setattr(clinic_outreach_sender, "email_configured", lambda: True)
    monkeypatch.setattr("app.services.clinic_outreach_service.email_configured", lambda: True)

    tid, cid, ids = await _ready_for_golive(("Alpha Clinic", "Beta Clinic", "Gamma Clinic"))
    clock = _Clock(datetime.now(UTC))
    _freeze(monkeypatch, clock)
    ctx = ExecutionContext(tenant_id=tid, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=Role.OWNER)
    async with async_session_maker() as s:
        s.add(Organization(id=tid, name="E2E", slug=f"e2e-{uuid.uuid4().hex}", plan="growth", billing_status="active"))
        await s.commit()

    assert (await _svc().go_live(tid, USER, cid, confirm=True))["mode"] == "LIVE"
    start = (datetime.now(UTC) + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    while start.weekday() != 0:
        start += timedelta(days=1)

    # Week one: introductions go out, at most 10 clinics, inside each clinic's local window, one per clinic.
    await _run_hours(tid, ctx, clock, start, 2)
    intros = [m for m in channel.sent if "Alpha Clinic" in m["body"] or "Beta Clinic" in m["body"] or "Gamma Clinic" in m["body"]]
    assert len(intros) == 3 and {m["to"] for m in intros} == {"info@alpha.example", "info@beta.example", "info@gamma.example"}
    async with async_session_maker() as s:
        sent = list((await s.execute(select(ClinicOutreachMessage).where(ClinicOutreachMessage.tenant_id == tid, ClinicOutreachMessage.status == "SENT"))).scalars())
    for m in sent:
        local = _utc(m.sent_at).astimezone(ZoneInfo("Europe/Istanbul"))
        assert 9 <= local.hour < 17 and local.weekday() < 5, local

    # Beta asks about terms, Gamma opts out (through the reply handler the inbound path uses).
    clock.now = start + timedelta(days=2, hours=8)
    async with async_session_maker() as s:
        pids = {n: (await s.execute(select(ClinicOutreach.provider_id).where(ClinicOutreach.id == oid))).scalar_one() for n, oid in ids.items()}
    await _svc().handle_reply(tid, USER, cid, pids["Beta Clinic"], "What commission do you pay?", now=clock.now)
    await _svc().handle_reply(tid, USER, cid, pids["Gamma Clinic"], "Please unsubscribe us", now=clock.now)
    before = len(channel.sent)
    await _run_hours(tid, ctx, clock, start + timedelta(days=2), 1)
    new = channel.sent[before:]
    assert len(new) == 2  # one answer to Beta with its terms, one confirmation to Gamma
    beta_answer = next(m for m in new if m["to"] == "info@beta.example")
    gamma_note = next(m for m in new if m["to"] == "info@gamma.example")
    assert "percent of the treatment price" in beta_answer["body"] and "will not contact you again" in gamma_note["body"]

    # The rest of the sequence: only Alpha keeps getting follow-ups; Beta (answered) and Gamma (opted out) are left alone.
    await _run_hours(tid, ctx, clock, start + timedelta(days=3), 30)
    by_clinic: dict[str, list[dict]] = {}
    for m in channel.sent:
        by_clinic.setdefault(m["to"], []).append(m)
    assert len(by_clinic["info@alpha.example"]) == 3  # intro, follow-up, last follow-up: never more
    assert len(by_clinic["info@beta.example"]) == 2 and len(by_clinic["info@gamma.example"]) == 2  # intro + the one reply each
    async with async_session_maker() as s:
        rows = {n: (await s.execute(select(ClinicOutreach).where(ClinicOutreach.id == oid))).scalar_one() for n, oid in ids.items()}
        sent_msgs = list((await s.execute(select(ClinicOutreachMessage).where(ClinicOutreachMessage.tenant_id == tid, ClinicOutreachMessage.status == "SENT").order_by(ClinicOutreachMessage.sent_at))).scalars())
        audit = list((await s.execute(select(AuditLog).where(AuditLog.tenant_id == tid, AuditLog.action == "outreach.message_sent"))).scalars())
    assert rows["Alpha Clinic"].stage == OutreachStage.NO_RESPONSE and rows["Alpha Clinic"].emails_sent == 3
    assert rows["Beta Clinic"].stage == OutreachStage.TERMS_PROPOSED and rows["Gamma Clinic"].stage == OutreachStage.OPTED_OUT
    alpha = [m for m in sent_msgs if m.outreach_id == ids["Alpha Clinic"]]
    assert [m.kind for m in alpha] == ["intro", "followup_1", "followup_2"]
    assert _utc(alpha[1].sent_at) - _utc(alpha[0].sent_at) >= timedelta(days=4) and _utc(alpha[2].sent_at) - _utc(alpha[1].sent_at) >= timedelta(days=5)
    assert len(audit) == len(sent_msgs) == len(channel.sent) and all("@" not in str(a.input_summary) for a in audit)
    live = (await _svc().get(tid, cid))["live"]
    assert live["sent_total"] == len(channel.sent) and live["opted_out"] == 1 and live["replies"] == 2

    # Safety stops: a paused campaign and the AI kill switch both send nothing.
    async with async_session_maker() as s:
        await s.execute(__import__("sqlalchemy").update(ClinicOutreach).where(ClinicOutreach.id == ids["Alpha Clinic"]).values(stage=OutreachStage.INTRO_SENT))
        s.add(ClinicOutreachMessage(tenant_id=tid, outreach_id=ids["Alpha Clinic"], kind="followup_1", status="QUEUED_LIVE", subject="s", body="b", blocks_version=0,
                                    scheduled_for=clock.now - timedelta(hours=1), schedule_day=(clock.now + timedelta(days=400)).date()))
        await s.commit()
    count = len(channel.sent)
    async with async_session_maker() as s:
        await s.execute(__import__("sqlalchemy").update(Organization).where(Organization.id == tid).values(ai_paused=True))
        await s.commit()
    await get_tool_registry().execute("outreach.run_engine", {}, ExecutionContext(tenant_id=tid, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=Role.OWNER))
    assert len(channel.sent) == count  # kill switch: nothing
    async with async_session_maker() as s:
        await s.execute(__import__("sqlalchemy").update(Organization).where(Organization.id == tid).values(ai_paused=False))
        await s.execute(__import__("sqlalchemy").update(Campaign).where(Campaign.id == cid).values(status=CampaignStatus.PAUSED))
        await s.commit()
    await get_tool_registry().execute("outreach.run_engine", {}, ctx)
    assert len(channel.sent) == count  # paused: nothing
    await _svc().stop(tid, USER, cid)
    async with async_session_maker() as s:
        assert not list((await s.execute(select(ClinicOutreachMessage).where(ClinicOutreachMessage.tenant_id == tid, ClinicOutreachMessage.status == "QUEUED_LIVE"))).scalars())  # stopped: nothing waits to be sent
