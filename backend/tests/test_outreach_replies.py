"""Reply handling (Phase 3) through the service and API: Try a reply stores nothing about a clinic, handle_reply applies the action table in shadow, nothing is ever sent."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.session import async_session_maker
from app.models.audit_log import AuditLog
from app.models.clinic_outreach import ClinicOutreach, ClinicOutreachCampaign, ClinicOutreachMessage, OutreachMode, OutreachStage, OutreachSuppression
from app.services.clinic_outreach_service import OutreachError, OutreachNotFound
from tests.test_clinic_outreach_engine import ENGINE, NOW, _campaign
from tests.test_clinic_outreach_phase1_end_to_end import SEND_PATHS, _resolve
from tests.test_clinic_outreach_service import USER, _svc
from tests.test_business_journey_api import _register
from tests.test_halla_integration import _h, _tenant_id
from tests.test_outreach_api import BASE, _service  # noqa: F401

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _no_sends(monkeypatch):
    calls = []
    for module, dotted in SEND_PATHS:
        target, attr = _resolve(module, dotted)
        monkeypatch.setattr(target, attr, lambda *a, _n=f"{module}.{dotted}", **k: calls.append(_n) or (_ for _ in ()).throw(AssertionError(_n)))
    yield calls
    assert calls == []


async def _setup(names=("Alpha Clinic",)):
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid, names)
    async with async_session_maker() as s:
        pids = {n: (await s.execute(select(ClinicOutreach.provider_id).where(ClinicOutreach.id == oid))).scalar_one() for n, oid in ids.items()}
    return tid, cid, ids, pids


def _utc(d: datetime) -> datetime:
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)


async def _row(oid) -> ClinicOutreach:
    async with async_session_maker() as s:
        return (await s.execute(select(ClinicOutreach).where(ClinicOutreach.id == oid))).scalar_one()


async def _msgs(oid, kind):
    async with async_session_maker() as s:
        return list((await s.execute(select(ClinicOutreachMessage).where(ClinicOutreachMessage.outreach_id == oid, ClinicOutreachMessage.kind == kind))).scalars())


async def test_try_reply_shows_the_answer_stores_nothing_and_counts_the_test() -> None:
    tid, cid, ids, pids = await _setup()
    out = await _svc().try_reply(tid, USER, cid, "What commission do you pay?")
    assert out["label"] == "ASKS_TERMS" and out["next_stage"] == "TERMS_PROPOSED" and "percent of the treatment price" in out["draft"]["body"]
    assert await _msgs(ids["Alpha Clinic"], "reply_out") == [] and await _msgs(ids["Alpha Clinic"], "reply_in") == []
    assert (await _row(ids["Alpha Clinic"])).stage == OutreachStage.READY
    async with async_session_maker() as s:
        cfg = (await s.execute(select(ClinicOutreachCampaign).where(ClinicOutreachCampaign.campaign_id == cid))).scalar_one()
        audit = list((await s.execute(select(AuditLog).where(AuditLog.tenant_id == tid, AuditLog.action == "outreach.reply_tested"))).scalars())
    assert cfg.exit_checks["reply_tests"] == {"terms": 1, "total": 1}
    assert len(audit) == 1 and "commission" not in str(audit[0].input_summary)


async def test_try_reply_for_a_clinic_uses_its_name_and_refuses_unknown_clinics_and_unapproved_wording() -> None:
    tid, cid, ids, pids = await _setup()
    out = await _svc().try_reply(tid, USER, cid, "We are interested", pids["Alpha Clinic"])
    assert "Alpha Clinic" in out["draft"]["body"]
    with pytest.raises(OutreachNotFound):
        await _svc().try_reply(tid, USER, cid, "hi", uuid.uuid4())
    with pytest.raises(OutreachError):
        await _svc().try_reply(tid, USER, cid, "   ")
    tid2, cid2, _, _ = await _setup()
    async with async_session_maker() as s:
        from app.models.tenant_settings import TenantSetting
        await s.execute(TenantSetting.__table__.delete().where(TenantSetting.tenant_id == tid2))
        await s.commit()
    with pytest.raises(OutreachError):
        await _svc().try_reply(tid2, USER, cid2, "We are interested")


async def test_golive_checks_follow_the_reply_tests_and_review_counts() -> None:
    tid, cid, ids, pids = await _setup()
    checks = (await _svc().shadow_progress(tid, cid))["golive_checks"]
    assert checks["all_ok"] is False and checks["reply_tests"]["covered"] == [] and checks["settings"]["ok"] is True
    for text in ("We are interested", "What commission do you pay?", "unsubscribe", "Our lawyer will write", "ok"):
        await _svc().try_reply(tid, USER, cid, text)
    checks = (await _svc().shadow_progress(tid, cid))["golive_checks"]
    assert checks["reply_tests"]["ok"] is True and checks["reply_tests"]["total"] == 5 and checks["shadow_days"]["ok"] is False and checks["all_ok"] is False


async def test_handle_reply_interested_stores_the_reply_and_a_shadow_draft_and_moves_the_clinic() -> None:
    tid, cid, ids, pids = await _setup()
    out = await _svc().handle_reply(tid, USER, cid, pids["Alpha Clinic"], "We are interested", now=NOW)
    assert out["next_stage"] == "REPLIED"
    row = await _row(ids["Alpha Clinic"])
    assert row.stage == OutreachStage.REPLIED and row.next_action_at is None and row.last_reply_at is not None and row.emails_sent == 0
    [inbound] = await _msgs(ids["Alpha Clinic"], "reply_in")
    [draft] = await _msgs(ids["Alpha Clinic"], "reply_out")
    assert inbound.direction == "IN" and inbound.status == "IN_INTERESTED" and draft.direction == "OUT" and draft.status == "QUEUED_SHADOW"


async def test_handle_reply_opt_out_suppresses_the_address_and_stops_everything() -> None:
    tid, cid, ids, pids = await _setup()
    await _svc().handle_reply(tid, USER, cid, pids["Alpha Clinic"], "Please unsubscribe us", now=NOW)
    row = await _row(ids["Alpha Clinic"])
    assert row.stage == OutreachStage.OPTED_OUT and row.next_action_at is None
    assert await _svc().is_suppressed(tid, row.contact_email)
    assert await ENGINE.run(tid, now=NOW + timedelta(days=10)) == {"campaigns": 1, "composed": 0, "blocked": 0, "skipped": 0, "parked": 0}  # nothing more is drafted for it


async def test_handle_reply_bounce_clears_the_address_and_auto_reply_defers_the_follow_up() -> None:
    tid, cid, ids, pids = await _setup(("Alpha Clinic", "Beta Clinic"))
    await ENGINE.run(tid, now=NOW)
    before = await _row(ids["Beta Clinic"])
    await _svc().handle_reply(tid, USER, cid, pids["Alpha Clinic"], "Delivery Status Notification (Failure)", now=NOW)
    a = await _row(ids["Alpha Clinic"])
    assert a.stage == OutreachStage.NO_EMAIL and a.contact_email is None and a.next_action_at is None
    assert before.stage == OutreachStage.INTRO_SENT
    await _svc().handle_reply(tid, USER, cid, pids["Beta Clinic"], "I am out of office", now=NOW)
    b = await _row(ids["Beta Clinic"])
    assert b.stage == OutreachStage.INTRO_SENT and _utc(b.next_action_at) >= _utc(before.next_action_at)


async def test_handle_reply_handoff_and_repeated_question_go_to_a_person() -> None:
    tid, cid, ids, pids = await _setup()
    await _svc().handle_reply(tid, USER, cid, pids["Alpha Clinic"], "Our lawyer will contact you", now=NOW)
    row = await _row(ids["Alpha Clinic"])
    assert row.stage == OutreachStage.NEEDS_PERSON and "legal" in row.handoff_reason.lower() and await _msgs(ids["Alpha Clinic"], "reply_out") == []
    tid, cid, ids, pids = await _setup()
    await _svc().handle_reply(tid, USER, cid, pids["Alpha Clinic"], "We are interested", now=NOW)
    await _svc().handle_reply(tid, USER, cid, pids["Alpha Clinic"], "Yes we are interested", now=NOW + timedelta(days=1))
    assert (await _row(ids["Alpha Clinic"])).stage == OutreachStage.NEEDS_PERSON
    with pytest.raises(OutreachError):  # a second reply on the same day is a duplicate, not a crash
        await _svc().handle_reply(tid, USER, cid, pids["Alpha Clinic"], "Yes we are interested", now=NOW + timedelta(days=1))


async def test_handle_reply_live_drafts_for_sending_other_tenants_get_404_and_audits_hold_labels_only() -> None:
    tid, cid, ids, pids = await _setup()
    other_tid = uuid.uuid4()
    with pytest.raises(OutreachNotFound):
        await _svc().handle_reply(other_tid, USER, cid, pids["Alpha Clinic"], "We are interested", now=NOW)
    await _svc().handle_reply(tid, USER, cid, pids["Alpha Clinic"], "We are interested", now=NOW)
    async with async_session_maker() as s:
        audit = list((await s.execute(select(AuditLog).where(AuditLog.tenant_id == tid, AuditLog.action == "outreach.reply_handled"))).scalars())
        assert len(audit) == 1 and "@" not in str(audit[0].input_summary) and "interested" not in str(audit[0].input_summary).replace("INTERESTED", "")
        from sqlalchemy import update
        await s.execute(update(ClinicOutreachCampaign).where(ClinicOutreachCampaign.campaign_id == cid).values(mode=OutreachMode.LIVE))
        await s.commit()
    await _svc().handle_reply(tid, USER, cid, pids["Alpha Clinic"], "Please send more information", now=NOW + timedelta(days=2))  # a live campaign answers too
    drafts = await _msgs(ids["Alpha Clinic"], "reply_out")
    assert sorted(d.status for d in drafts) == ["QUEUED_LIVE", "QUEUED_SHADOW"]  # the earlier shadow answer stays shadow; the live one is queued for sending


async def test_try_reply_api_permissions_validation_and_tenant_isolation(client) -> None:
    from tests.test_agent_api import _readonly_token
    token = await _register(client, "Org Replies", "replies@example.com")
    tid = await _tenant_id(client, token)
    cid, _ = await _campaign(tid, ("Alpha Clinic",))
    h = _h(token)
    ok = await client.post(f"{BASE}/campaigns/{cid}/try-reply", json={"text": "Who are you?"}, headers=h)
    assert ok.status_code == 200 and ok.json()["label"] == "ASKS_WHO" and ok.json()["draft"]["body"]
    assert (await client.post(f"{BASE}/campaigns/{cid}/try-reply", json={"text": ""}, headers=h)).status_code == 422
    assert (await client.post(f"{BASE}/campaigns/{cid}/try-reply", json={"text": "x" * 5001}, headers=h)).status_code == 422
    ro = await _readonly_token(client, tid, "ro-replies@example.com")
    assert (await client.post(f"{BASE}/campaigns/{cid}/try-reply", json={"text": "hi"}, headers=_h(ro))).status_code == 403
    other = await _register(client, "Other Replies", "replies-b@example.com")
    assert (await client.post(f"{BASE}/campaigns/{cid}/try-reply", json={"text": "hi"}, headers=_h(other))).status_code in (404, 409)
    assert (await client.post(f"{BASE}/campaigns/{cid}/try-reply", json={"text": "hi"})).status_code == 401
