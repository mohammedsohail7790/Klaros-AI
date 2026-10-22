"""Go live: refused until the owner confirms and every check passes and a real email provider exists; then shadow drafts are archived, clinics restart, and the live funnel appears."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.db.session import async_session_maker
from app.models.audit_log import AuditLog
from app.models.clinic_outreach import ClinicOutreach, ClinicOutreachCampaign, ClinicOutreachMessage, OutreachMessageStatus, OutreachMode, OutreachStage
from app.models.notification import Notification
from app.services.clinic_outreach_service import OutreachError
from tests.test_clinic_outreach_engine import ENGINE, _campaign
from tests.test_clinic_outreach_service import USER, _svc

pytestmark = pytest.mark.asyncio


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr("app.services.clinic_outreach_service.email_configured", lambda: True)


async def _ready_for_golive(names=("Alpha Clinic", "Beta Clinic")):
    """A shadow campaign that has passed every go-live check."""
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid, names)
    now = datetime.now(UTC)
    async with async_session_maker() as s:
        await s.execute(update(ClinicOutreachCampaign).where(ClinicOutreachCampaign.campaign_id == cid).values(shadow_started_at=now - timedelta(days=8)))
        first = next(iter(ids.values()))
        for i in range(20):  # twenty reviewed drafts, none flagged
            day = (now - timedelta(days=i + 1))
            s.add(ClinicOutreachMessage(tenant_id=tid, outreach_id=first, kind="followup_2" if i % 2 else "followup_1", status=OutreachMessageStatus.QUEUED_SHADOW, subject="s", body="b",
                                        blocks_version=0, scheduled_for=day, schedule_day=(now + timedelta(days=500 + i)).date(), review_mark="GOOD"))
        await s.commit()
    for text in ("We are interested", "What commission do you pay?", "unsubscribe", "Our lawyer will write", "ok"):
        await _svc().try_reply(tid, USER, cid, text)
    return tid, cid, ids


async def test_go_live_needs_confirmation_a_provider_a_shadow_campaign_and_passing_checks(provider, monkeypatch) -> None:
    tid, cid, ids = await _ready_for_golive()
    with pytest.raises(OutreachError, match="Confirm"):
        await _svc().go_live(tid, USER, cid, confirm=False)
    monkeypatch.setattr("app.services.clinic_outreach_service.email_configured", lambda: False)
    with pytest.raises(OutreachError, match="email provider"):
        await _svc().go_live(tid, USER, cid, confirm=True)
    monkeypatch.setattr("app.services.clinic_outreach_service.email_configured", lambda: True)
    tid2, cid2, _ = await _campaign_not_ready()
    with pytest.raises(OutreachError, match="checks"):
        await _svc().go_live(tid2, USER, cid2, confirm=True)


async def _campaign_not_ready():
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid, ("Alpha Clinic",))
    return tid, cid, ids


async def test_go_live_archives_shadow_drafts_restarts_clinics_and_audits(provider) -> None:
    tid, cid, ids = await _ready_for_golive()
    now = datetime.now(UTC)
    await ENGINE.run(tid, now=now)  # shadow drafts exist and the clinics sit in INTRO_SENT
    out = await _svc().go_live(tid, USER, cid, confirm=True)
    assert out["mode"] == "LIVE" and out["clinics_restarted"] == 2
    async with async_session_maker() as s:
        cfg = (await s.execute(select(ClinicOutreachCampaign).where(ClinicOutreachCampaign.campaign_id == cid))).scalar_one()
        rows = list((await s.execute(select(ClinicOutreach).where(ClinicOutreach.campaign_id == cid))).scalars())
        shadow_left = list((await s.execute(select(ClinicOutreachMessage).where(ClinicOutreachMessage.tenant_id == tid, ClinicOutreachMessage.status == OutreachMessageStatus.QUEUED_SHADOW))).scalars())
        audit = list((await s.execute(select(AuditLog).where(AuditLog.tenant_id == tid, AuditLog.action == "outreach.went_live"))).scalars())
        notes = list((await s.execute(select(Notification).where(Notification.tenant_id == tid, Notification.category == "outreach"))).scalars())
    assert cfg.mode == OutreachMode.LIVE and cfg.live_at is not None
    assert all(r.stage == OutreachStage.READY and r.next_action_at is None and r.emails_sent == 0 for r in rows)
    assert shadow_left == [] and len(audit) == 1 and len(notes) == 1
    again = await _svc().get(tid, cid)
    assert again["mode"] == "LIVE" and again["live"]["sent_total"] == 0
    with pytest.raises(OutreachError):  # already live
        await _svc().go_live(tid, USER, cid, confirm=True)
    # the engine now drafts real introductions, not stale shadow ones
    await ENGINE.run(tid, now=datetime.now(UTC))
    async with async_session_maker() as s:
        live = list((await s.execute(select(ClinicOutreachMessage).where(ClinicOutreachMessage.tenant_id == tid, ClinicOutreachMessage.status == OutreachMessageStatus.QUEUED_LIVE))).scalars())
    assert len(live) >= 1 and all(m.kind == "intro" for m in live)


async def test_go_live_is_tenant_scoped_and_a_paused_campaign_cannot_go_live(provider) -> None:
    tid, cid, _ = await _ready_for_golive()
    from app.services.clinic_outreach_service import OutreachNotFound
    with pytest.raises(OutreachNotFound):
        await _svc().go_live(uuid.uuid4(), USER, cid, confirm=True)
    await _svc().pause(tid, USER, cid)
    with pytest.raises(OutreachError, match="shadow"):
        await _svc().go_live(tid, USER, cid, confirm=True)
