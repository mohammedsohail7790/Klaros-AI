"""The live sender: sends each queued email exactly once, only when everything allows it, re-checks the world at send time, retries once, and stops itself when bounces or opt-outs pile up."""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.db.session import async_session_maker
from app.models.audit_log import AuditLog
from app.models.clinic_outreach import ClinicOutreach, ClinicOutreachCampaign, ClinicOutreachMessage, OutreachMessageStatus, OutreachMode, OutreachStage, OutreachSuppression
from app.models.marketing import Campaign, CampaignStatus
from app.models.notification import Notification
from app.models.organization import Organization
from app.services.clinic_outreach_sender import ClinicOutreachSender
from app.tools.builtin.mt_referral_tools import INVITE_ACTION
from tests.test_clinic_outreach_engine import ENGINE, NOW, _campaign, _clinic_row, _messages, _set_cfg, _utc

pytestmark = pytest.mark.asyncio

BIG = {"daily_cap": 50, "ramp_start": 50}


class FakeChannel:
    def __init__(self, results=None):
        self.sent: list[dict] = []
        self.results = list(results or [])

    async def send_email(self, tenant_id, *, to, subject, body, template):
        outcome = self.results.pop(0) if self.results else True
        if isinstance(outcome, Exception):
            raise outcome
        if outcome:
            self.sent.append({"tenant_id": tenant_id, "to": to, "subject": subject, "body": body})
        return outcome


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr("app.services.clinic_outreach_sender.email_configured", lambda: True)


def _sender(channel):
    return ClinicOutreachSender(async_session_maker, channel_factory=lambda: channel)


async def _live(names=("Alpha Clinic",), limits=BIG):
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid, names)
    await _set_cfg(cid, mode=OutreachMode.LIVE, live_at=NOW - timedelta(days=30), limits=limits)
    await ENGINE.run(tid, now=NOW)  # drafts QUEUED_LIVE intros due at NOW
    return tid, cid, ids


async def _by_status(tid):
    out: dict[str, list] = {}
    for m in await _messages(tid=tid):
        out.setdefault(m.status, []).append(m)
    return out


async def test_each_due_message_is_sent_once_and_recorded() -> None:
    tid, cid, ids = await _live(("Alpha Clinic", "Beta Clinic"))
    ch = FakeChannel()
    out = await _sender(ch).send_due(tid, now=NOW)
    assert out["sent"] == 2 and len(ch.sent) == 2 and {s["to"] for s in ch.sent} == {"info@alpha.example", "info@beta.example"}
    assert all("Alpha Clinic" in s["body"] or "Beta Clinic" in s["body"] for s in ch.sent)
    row = await _clinic_row(ids["Alpha Clinic"])
    assert row.emails_sent == 1 and _utc(row.last_sent_at) == NOW
    assert all(m.status == OutreachMessageStatus.SENT and m.sent_at is not None and m.attempts == 1 for m in await _messages(tid=tid))
    async with async_session_maker() as s:
        audit = list((await s.execute(select(AuditLog).where(AuditLog.tenant_id == tid, AuditLog.action == "outreach.message_sent"))).scalars())
    assert len(audit) == 2 and all("@" not in str(a.input_summary) for a in audit)
    again = await _sender(ch).send_due(tid, now=NOW + timedelta(minutes=5))
    assert again["sent"] == 0 and len(ch.sent) == 2  # never twice


async def test_concurrent_runs_send_each_message_once() -> None:
    tid, cid, ids = await _live(("Alpha Clinic", "Beta Clinic"))
    ch = FakeChannel()
    await asyncio.gather(*[_sender(ch).send_due(tid, now=NOW) for _ in range(4)])
    assert len(ch.sent) == 2


@pytest.mark.parametrize("what", ["shadow", "paused", "kill_switch", "not_due"])
async def test_nothing_is_sent_unless_active_live_unkilled_and_due(what) -> None:
    tid, cid, ids = await _live()
    when = NOW
    if what == "shadow":
        await _set_cfg(cid, mode=OutreachMode.SHADOW)
    elif what == "paused":
        async with async_session_maker() as s:
            await s.execute(update(Campaign).where(Campaign.id == cid).values(status=CampaignStatus.PAUSED))
            await s.commit()
    elif what == "kill_switch":
        async with async_session_maker() as s:
            s.add(Organization(id=tid, name="K", slug=f"k-{uuid.uuid4().hex}", ai_paused=True, plan="growth", billing_status="active"))
            await s.commit()
    else:
        when = NOW - timedelta(hours=3)
    ch = FakeChannel()
    await _sender(ch).send_due(tid, now=when)
    assert ch.sent == [] and (await _by_status(tid)).get("SENT") is None


async def test_nothing_is_sent_without_a_real_email_provider(monkeypatch) -> None:
    tid, cid, ids = await _live()
    monkeypatch.setattr("app.services.clinic_outreach_sender.email_configured", lambda: False)
    ch = FakeChannel()
    out = await _sender(ch).send_due(tid, now=NOW)
    assert ch.sent == [] and out["held"] == 1


async def test_an_address_suppressed_after_drafting_is_cancelled_and_the_clinic_handed_to_a_person() -> None:
    tid, cid, ids = await _live()
    async with async_session_maker() as s:
        s.add(OutreachSuppression(tenant_id=tid, kind="domain", value="alpha.example", reason="asked"))
        await s.commit()
    ch = FakeChannel()
    out = await _sender(ch).send_due(tid, now=NOW)
    assert ch.sent == [] and out["cancelled"] == 1
    row = await _clinic_row(ids["Alpha Clinic"])
    assert row.stage == OutreachStage.NEEDS_PERSON and "suppression" in row.handoff_reason
    assert (await _by_status(tid))["CANCELLED"]


async def test_a_clinic_that_replied_gets_no_follow_up_but_the_closing_confirmation_to_an_opt_out_is_sent() -> None:
    tid, cid, ids = await _live()
    oid = ids["Alpha Clinic"]
    async with async_session_maker() as s:  # a follow-up is queued, then the clinic replies "interested" -> REPLIED
        s.add(ClinicOutreachMessage(tenant_id=tid, outreach_id=oid, kind="followup_1", status=OutreachMessageStatus.QUEUED_LIVE, subject="f", body="b", blocks_version=0,
                                    scheduled_for=NOW, schedule_day=(NOW + timedelta(days=1)).date()))
        await s.execute(update(ClinicOutreach).where(ClinicOutreach.id == oid).values(stage=OutreachStage.REPLIED))
        await s.commit()
    ch = FakeChannel()
    out = await _sender(ch).send_due(tid, now=NOW)
    assert out["cancelled"] == 2 and ch.sent == []  # the queued intro and the follow-up are both cancelled
    # opt-out: the address is suppressed, yet the single confirmation goes out
    async with async_session_maker() as s:
        s.add(OutreachSuppression(tenant_id=tid, kind="address", value="info@alpha.example", reason="clinic reply: opt_out"))
        s.add(ClinicOutreachMessage(tenant_id=tid, outreach_id=oid, kind="reply_out", status=OutreachMessageStatus.QUEUED_LIVE, subject="Re", body="We will not contact you again.", blocks_version=0,
                                    scheduled_for=NOW, schedule_day=NOW.date()))
        await s.execute(update(ClinicOutreach).where(ClinicOutreach.id == oid).values(stage=OutreachStage.OPTED_OUT))
        await s.commit()
    out = await _sender(ch).send_due(tid, now=NOW)
    assert out["sent"] == 1 and len(ch.sent) == 1 and ch.sent[0]["subject"] == "Re"


async def test_a_recent_onboarder_invitation_defers_the_message() -> None:
    tid, cid, ids = await _live()
    async with async_session_maker() as s:
        pid = (await s.execute(select(ClinicOutreach.provider_id).where(ClinicOutreach.id == ids["Alpha Clinic"]))).scalar_one()
        s.add(AuditLog(tenant_id=tid, actor_type="USER", actor_id=None, action=INVITE_ACTION, entity_type="medical_tourism_provider", entity_id=pid, input_summary={}, result="success",
                       created_at=NOW - timedelta(days=2)))
        await s.commit()
    ch = FakeChannel()
    out = await _sender(ch).send_due(tid, now=NOW)
    assert out["deferred"] == 1 and ch.sent == []
    [m] = (await _by_status(tid))["QUEUED_LIVE"]
    assert _utc(m.scheduled_for) >= NOW + timedelta(days=11)


async def test_the_daily_cap_counts_sent_emails_but_never_holds_back_replies() -> None:
    tid, cid, ids = await _live(("Alpha Clinic", "Beta Clinic"), limits={"daily_cap": 1, "ramp_start": 1})
    ch = FakeChannel()
    msgs = await _messages(tid=tid)
    assert len(msgs) == 1  # the engine itself drafted only one for the day
    async with async_session_maker() as s:  # a second introduction was queued by hand, plus a reply to a clinic
        second = next(i for i in ids.values() if i != msgs[0].outreach_id)  # the clinic the engine did NOT draft for (which one that is depends on the random ids)
        s.add(ClinicOutreachMessage(tenant_id=tid, outreach_id=second, kind="intro", status=OutreachMessageStatus.QUEUED_LIVE, subject="i", body="b", blocks_version=0, scheduled_for=NOW, schedule_day=NOW.date()))
        s.add(ClinicOutreachMessage(tenant_id=tid, outreach_id=second, kind="reply_out", status=OutreachMessageStatus.QUEUED_LIVE, subject="r", body="b", blocks_version=0, scheduled_for=NOW, schedule_day=NOW.date()))
        await s.execute(update(ClinicOutreach).where(ClinicOutreach.id == second).values(stage=OutreachStage.INTRO_SENT))
        await s.commit()
    out = await _sender(ch).send_due(tid, now=NOW)
    assert out["sent"] == 2 and out["deferred"] == 1  # the reply and one introduction; the other introduction waits for tomorrow
    assert {s["subject"] for s in ch.sent} >= {"r"}


async def test_a_failed_send_is_retried_the_next_day_and_a_second_failure_hands_the_clinic_to_a_person() -> None:
    tid, cid, ids = await _live()
    ch = FakeChannel([False])
    out = await _sender(ch).send_due(tid, now=NOW)
    assert out["failed"] == 1 and ch.sent == []
    [m] = (await _by_status(tid))["QUEUED_LIVE"]
    assert m.attempts == 1 and _utc(m.scheduled_for) == NOW + timedelta(days=1)
    assert (await _sender(ch).send_due(tid, now=NOW + timedelta(hours=5)))["sent"] == 0  # not before the retry time
    ch.results = [RuntimeError("provider down")]
    out = await _sender(ch).send_due(tid, now=NOW + timedelta(days=1))
    assert out["failed"] == 1
    [m] = (await _by_status(tid))["FAILED"]
    assert m.attempts == 2
    row = await _clinic_row(ids["Alpha Clinic"])
    assert row.stage == OutreachStage.NEEDS_PERSON and "two tries" in row.handoff_reason and row.emails_sent == 0
    async with async_session_maker() as s:
        assert list((await s.execute(select(Notification).where(Notification.tenant_id == tid, Notification.category == "outreach"))).scalars())


async def test_a_retry_that_succeeds_is_sent_once() -> None:
    tid, cid, ids = await _live()
    ch = FakeChannel([False, True])
    await _sender(ch).send_due(tid, now=NOW)
    out = await _sender(ch).send_due(tid, now=NOW + timedelta(days=1))
    assert out["sent"] == 1 and len(ch.sent) == 1 and (await _clinic_row(ids["Alpha Clinic"])).emails_sent == 1


async def test_too_many_bounces_pause_the_campaign_and_nothing_more_is_sent() -> None:
    tid, cid, ids = await _live(("Alpha Clinic", "Beta Clinic"))
    oid = ids["Alpha Clinic"]
    async with async_session_maker() as s:
        for i in range(10):
            d = NOW - timedelta(days=i + 1)
            s.add(ClinicOutreachMessage(tenant_id=tid, outreach_id=oid, kind="followup_1" if i % 2 else "followup_2", status=OutreachMessageStatus.SENT, subject="s", body="b", blocks_version=0,
                                        scheduled_for=d, schedule_day=(NOW + timedelta(days=300 + i)).date(), sent_at=d))
        s.add(ClinicOutreachMessage(tenant_id=tid, outreach_id=oid, direction="IN", kind="reply_in", status="IN_BOUNCE", subject="x", body="bounce", blocks_version=0,
                                    scheduled_for=NOW, schedule_day=NOW.date()))
        await s.commit()
    ch = FakeChannel()
    out = await _sender(ch).send_due(tid, now=NOW)
    assert out["paused"] == 1 and ch.sent == []
    async with async_session_maker() as s:
        assert (await s.execute(select(Campaign.status).where(Campaign.id == cid))).scalar_one() == CampaignStatus.PAUSED
        audit = list((await s.execute(select(AuditLog).where(AuditLog.tenant_id == tid, AuditLog.action == "outreach.auto_paused"))).scalars())
    assert len(audit) == 1
    assert (await _sender(ch).send_due(tid, now=NOW + timedelta(hours=1)))["campaigns"] == 0  # paused stays paused until a person resumes it


async def test_another_tenant_sends_nothing() -> None:
    tid, cid, ids = await _live()
    ch = FakeChannel()
    out = await _sender(ch).send_due(uuid.uuid4(), now=NOW)
    assert out["campaigns"] == 0 and ch.sent == []
