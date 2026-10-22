"""Clinic outreach shadow engine: stores drafts only, never sends.

What must hold: one draft per clinic and kind however often or concurrently the engine runs, the sequence ends in NO_RESPONSE after three drafts,
emails_sent and last_sent_at are never touched, the daily cap with warm-up is honoured, a pause/stop/kill switch stops the run (even mid-run),
unsafe clinics are parked with a plain reason, one clinic's failure never stops the others, and tenants never see each other's work. SQLite."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.communications.factory import get_clinic_channel  # noqa: F401  (imported so the patch below can assert it exists)
from app.db.session import async_session_maker
from app.models.audit_log import AuditLog
from app.models.clinic_outreach import (ClinicOutreach, ClinicOutreachCampaign, ClinicOutreachMessage, OutreachMessageKind, OutreachMessageStatus,
                                        OutreachMode, OutreachStage)
from app.models.marketing import Campaign, CampaignStatus
from app.models.medical_tourism import Provider
from app.models.organization import Organization
from app.services import clinic_outreach_engine
from app.services.clinic_outreach_engine import ClinicOutreachEngine
from app.services.outreach_schedule import FOLLOWUP_GAPS, NO_RESPONSE_AFTER
from app.tools.builtin.mt_referral_tools import INVITE_ACTION
from tests.test_clinic_outreach_phase1_end_to_end import SEND_PATHS, _resolve
from tests.test_clinic_outreach_service import USER, _approve_default_blocks, _clinic, _config, _svc

NOW = datetime(2026, 11, 2, 7, 0, tzinfo=UTC)  # Monday 10:00 in Istanbul: inside the 09:00-17:00 window
ENGINE = ClinicOutreachEngine(async_session_maker)


async def _campaign(tid, names=("Alpha Clinic",), *, country="TR", approve=True, start=True, shadow_started=NOW):
    """An ACTIVE shadow campaign whose clinics are READY with an address; returns (campaign_id, {name: outreach_id})."""
    for n in names:
        await _clinic(tid, n, country=country)
    cid = await _svc().create_campaign(tid, USER, name="Turkey", config=_config((country,)), provider_ids=None)
    if approve:
        await _approve_default_blocks(tid)
    async with async_session_maker() as s:
        rows = (await s.execute(select(ClinicOutreach, ClinicOutreach.provider_id).where(ClinicOutreach.campaign_id == cid))).all()
        from app.models.medical_tourism import Provider
        names_by_provider = {p.id: p.name for p in (await s.execute(select(Provider).where(Provider.tenant_id == tid))).scalars()}
        ids = {}
        for o, pid in rows:
            name = names_by_provider[pid]
            o.stage, o.contact_email = OutreachStage.READY, f"info@{name.split()[0].lower()}.example"
            ids[name] = o.id
        await s.commit()
    if start and approve:
        await _svc().start_shadow(tid, USER, cid)
    async with async_session_maker() as s:
        await s.execute(update(ClinicOutreachCampaign).where(ClinicOutreachCampaign.campaign_id == cid).values(shadow_started_at=shadow_started))
        await s.commit()
    return cid, ids


async def _messages(outreach_id=None, tid=None):
    async with async_session_maker() as s:
        q = select(ClinicOutreachMessage).order_by(ClinicOutreachMessage.scheduled_for, ClinicOutreachMessage.kind)
        if outreach_id is not None:
            q = q.where(ClinicOutreachMessage.outreach_id == outreach_id)
        if tid is not None:
            q = q.where(ClinicOutreachMessage.tenant_id == tid)
        return list((await s.execute(q)).scalars())


async def _clinic_row(outreach_id) -> ClinicOutreach:
    async with async_session_maker() as s:
        return (await s.execute(select(ClinicOutreach).where(ClinicOutreach.id == outreach_id))).scalar_one()


async def _set_campaign(cid, **values):
    async with async_session_maker() as s:
        await s.execute(update(Campaign).where(Campaign.id == cid).values(**values))
        await s.commit()


async def _set_cfg(cid, **values):
    async with async_session_maker() as s:
        await s.execute(update(ClinicOutreachCampaign).where(ClinicOutreachCampaign.campaign_id == cid).values(**values))
        await s.commit()


def _utc(dt):
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


async def test_a_ready_clinic_gets_one_intro_draft_and_advances() -> None:
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid)
    out = await ENGINE.run(tid, now=NOW)
    assert out == {"campaigns": 1, "composed": 1, "blocked": 0, "skipped": 0, "parked": 0}
    [m] = await _messages(ids["Alpha Clinic"])
    assert (m.kind, m.status, m.direction) == (OutreachMessageKind.INTRO, OutreachMessageStatus.QUEUED_SHADOW, "OUT")
    assert _utc(m.scheduled_for) == NOW and m.schedule_day == NOW.date() and m.blocks_version == 0  # the platform default wording is version 0
    assert "Alpha Clinic" in m.body and m.subject
    row = await _clinic_row(ids["Alpha Clinic"])
    assert row.stage == OutreachStage.INTRO_SENT
    assert _utc(row.next_action_at) == NOW + FOLLOWUP_GAPS[OutreachMessageKind.FOLLOWUP_1]
    async with async_session_maker() as s:
        cfg = (await s.execute(select(ClinicOutreachCampaign).where(ClinicOutreachCampaign.campaign_id == cid))).scalar_one()
    assert _utc(cfg.engine_last_run_at) == NOW


async def test_the_whole_sequence_over_simulated_days_ends_in_no_response() -> None:
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid)
    oid = ids["Alpha Clinic"]
    day = NOW
    stages = []
    for _ in range(11):
        await ENGINE.run(tid, now=day)
        stages.append((await _clinic_row(oid)).stage)
        day += timedelta(days=1)
    for _ in range(14):
        await ENGINE.run(tid, now=day)
        day += timedelta(days=1)
    msgs = await _messages(oid)
    assert [m.kind for m in msgs] == ["intro", "followup_1", "followup_2"]
    assert stages[0] == OutreachStage.INTRO_SENT and OutreachStage.FOLLOWUP_1 in stages and OutreachStage.FOLLOWUP_2 in stages
    gaps = [_utc(b.scheduled_for) - _utc(a.scheduled_for) for a, b in zip(msgs, msgs[1:])]
    assert gaps[0] >= FOLLOWUP_GAPS[OutreachMessageKind.FOLLOWUP_1] and gaps[1] >= FOLLOWUP_GAPS[OutreachMessageKind.FOLLOWUP_2]
    row = await _clinic_row(oid)
    assert row.stage == OutreachStage.NO_RESPONSE and len(msgs) == 3


async def test_emails_sent_and_last_sent_at_are_never_changed() -> None:
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid)
    day = NOW
    for _ in range(20):
        await ENGINE.run(tid, now=day)
        day += timedelta(days=1)
    row = await _clinic_row(ids["Alpha Clinic"])
    assert row.emails_sent == 0 and row.last_sent_at is None and row.stage == OutreachStage.NO_RESPONSE


async def test_running_twice_makes_one_draft_per_clinic_and_kind() -> None:
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid, ("Alpha Clinic", "Beta Clinic"))
    first = await ENGINE.run(tid, now=NOW)
    second = await ENGINE.run(tid, now=NOW)
    assert first["composed"] == 2 and second["composed"] == 0
    assert len(await _messages(tid=tid)) == 2


async def test_concurrent_runs_make_one_draft_per_clinic_and_kind() -> None:
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid, ("Alpha Clinic", "Beta Clinic", "Gamma Clinic"))
    results = await asyncio.gather(ENGINE.run(tid, now=NOW), ENGINE.run(tid, now=NOW), ENGINE.run(tid, now=NOW + timedelta(minutes=5)))
    assert sum(r["composed"] for r in results) == 3
    msgs = await _messages(tid=tid)
    assert len(msgs) == 3 and len({m.outreach_id for m in msgs}) == 3


async def test_a_clinic_that_already_has_a_message_of_that_kind_is_not_drafted_again_even_with_a_new_slot() -> None:
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid)
    oid = ids["Alpha Clinic"]
    await ENGINE.run(tid, now=NOW)
    async with async_session_maker() as s:  # simulate a lost stage update: the clinic is READY again but its intro draft exists
        await s.execute(update(ClinicOutreach).where(ClinicOutreach.id == oid).values(stage=OutreachStage.READY, next_action_at=None))
        await s.commit()
    out = await ENGINE.run(tid, now=NOW + timedelta(days=1))  # a different slot and UTC day, so the unique key alone would not catch it
    assert out["composed"] == 0 and out["skipped"] == 1
    assert len(await _messages(oid)) == 1
    row = await _clinic_row(oid)
    assert row.stage == OutreachStage.INTRO_SENT  # healed to match the draft that exists
    assert _utc(row.next_action_at) == NOW + FOLLOWUP_GAPS[OutreachMessageKind.FOLLOWUP_1]


async def test_the_daily_cap_and_warmup_limit_drafts() -> None:
    tid = uuid.uuid4()
    names = tuple(f"Clinic{c} Clinic" for c in "ABCDEFGH")
    cid, ids = await _campaign(tid, names)  # shadow_started == NOW: warm-up day 0 allows ramp_start = 5
    out = await ENGINE.run(tid, now=NOW)
    assert out["composed"] == 5 and out["skipped"] == 3
    assert (await ENGINE.run(tid, now=NOW))["composed"] == 0  # the window already holds 5
    later = await ENGINE.run(tid, now=NOW + timedelta(days=2))  # day 2 allows 5 + 4 = 9, capped by daily_cap 20; 3 remain
    assert later["composed"] == 3
    # an explicit smaller daily cap wins over the ramp
    tid2 = uuid.uuid4()
    cid2, _ = await _campaign(tid2, names)
    await _set_cfg(cid2, limits={"daily_cap": 2, "ramp_start": 2})
    assert (await ENGINE.run(tid2, now=NOW))["composed"] == 2


async def test_paused_stopped_or_unapproved_campaigns_make_nothing() -> None:
    for status in (CampaignStatus.PAUSED, CampaignStatus.COMPLETED, CampaignStatus.DRAFT):
        tid = uuid.uuid4()
        cid, ids = await _campaign(tid)
        await _set_campaign(cid, status=status)
        assert await ENGINE.run(tid, now=NOW) == {"campaigns": 0, "composed": 0, "blocked": 0, "skipped": 0, "parked": 0}
        assert await _messages(tid=tid) == []
    # approved wording is required: an ACTIVE campaign whose wording is not approved is skipped, counted once
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid)
    async with async_session_maker() as s:
        from app.models.tenant_settings import TenantSetting
        rows = (await s.execute(select(TenantSetting).where(TenantSetting.tenant_id == tid))).scalars().all()
        for r in rows:
            r.value = {**r.value, "status": "DRAFT"}
        await s.commit()
    out = await ENGINE.run(tid, now=NOW)
    assert out == {"campaigns": 1, "composed": 0, "blocked": 0, "skipped": 1, "parked": 0} and await _messages(tid=tid) == []


async def test_a_status_change_during_the_run_stops_it() -> None:
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid, ("Alpha Clinic", "Beta Clinic", "Gamma Clinic"))

    class Flipping(ClinicOutreachEngine):
        calls = 0

        async def _between_clinics(self, tenant_id, campaign_id, outreach_id) -> None:
            self.calls += 1
            if self.calls == 2:  # someone pauses the campaign after the first clinic
                await _set_campaign(campaign_id, status=CampaignStatus.PAUSED)

    out = await Flipping(async_session_maker).run(tid, now=NOW)
    assert out["composed"] == 1 and out["skipped"] == 2
    assert len(await _messages(tid=tid)) == 1


async def test_kill_switch_makes_nothing() -> None:
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid)
    async with async_session_maker() as s:
        s.add(Organization(id=tid, name="Org", slug=f"o-{tid.hex[:8]}", ai_paused=True))
        await s.commit()
    out = await ENGINE.run(tid, now=NOW)
    assert out["composed"] == 0 and out["skipped"] == 1 and await _messages(tid=tid) == []
    # turning it off resumes
    async with async_session_maker() as s:
        await s.execute(update(Organization).where(Organization.id == tid).values(ai_paused=False))
        await s.commit()
    assert (await ENGINE.run(tid, now=NOW))["composed"] == 1


async def test_the_kill_switch_flipped_mid_run_stops_it() -> None:
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid, ("Alpha Clinic", "Beta Clinic"))
    async with async_session_maker() as s:
        s.add(Organization(id=tid, name="Org", slug=f"o-{tid.hex[:8]}", ai_paused=False))
        await s.commit()

    class Flipping(ClinicOutreachEngine):
        calls = 0

        async def _between_clinics(self, tenant_id, campaign_id, outreach_id) -> None:
            self.calls += 1
            if self.calls == 2:
                async with async_session_maker() as s:
                    await s.execute(update(Organization).where(Organization.id == tenant_id).values(ai_paused=True))
                    await s.commit()

    out = await Flipping(async_session_maker).run(tid, now=NOW)
    assert out["composed"] == 1 and out["skipped"] == 1


async def test_a_live_campaign_drafts_queued_live_messages_paced_from_live_at_and_stops_at_ten_clinics_in_week_one() -> None:
    tid = uuid.uuid4()
    names = tuple(f"Clinic {c}" for c in "ABCDEFGHIJKL")
    cid, ids = await _campaign(tid, names)
    await _set_cfg(cid, mode=OutreachMode.LIVE, live_at=NOW, limits={"daily_cap": 50, "ramp_start": 50})
    out = await ENGINE.run(tid, now=NOW)
    msgs = await _messages(tid=tid)
    assert out["composed"] == 10 and out["skipped"] == 2  # the first live week reaches at most 10 clinics
    assert len(msgs) == 10 and all(m.status == OutreachMessageStatus.QUEUED_LIVE and m.kind == "intro" for m in msgs)
    for oid in ids.values():
        assert (await _clinic_row(oid)).emails_sent == 0  # drafting never counts as sending


async def test_live_pacing_starts_at_the_ramp_and_shadow_archived_drafts_are_ignored() -> None:
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid, ("Alpha Clinic", "Beta Clinic", "Gamma Clinic"))
    await _set_cfg(cid, mode=OutreachMode.LIVE, live_at=NOW, limits={"daily_cap": 10, "ramp_start": 1})
    async with async_session_maker() as s:  # an archived shadow intro from before Go live must not count as the live intro or against the cap
        s.add(ClinicOutreachMessage(tenant_id=tid, outreach_id=ids["Alpha Clinic"], kind="intro", status=OutreachMessageStatus.SHADOW_ARCHIVED, subject="old", body="old",
                                    blocks_version=0, scheduled_for=NOW, schedule_day=NOW.date()))
        await s.commit()
    out = await ENGINE.run(tid, now=NOW)
    assert out["composed"] == 1  # day 0 of live: one draft only
    live = [m for m in await _messages(tid=tid) if m.status == OutreachMessageStatus.QUEUED_LIVE]
    assert len(live) == 1


async def test_an_invalid_stored_config_skips_the_campaign() -> None:
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid)
    await _set_cfg(cid, sender={"display_name": ""})
    out = await ENGINE.run(tid, now=NOW)
    assert out["skipped"] == 1 and out["composed"] == 0


async def test_suppressed_address_parks_with_a_reason() -> None:
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid, ("Alpha Clinic", "Beta Clinic"))
    await _svc().suppress(tid, "alpha.example", "domain", "asked us to stop")
    out = await ENGINE.run(tid, now=NOW)
    assert out["parked"] == 1 and out["composed"] == 1
    row = await _clinic_row(ids["Alpha Clinic"])
    assert row.stage == OutreachStage.NEEDS_PERSON and row.handoff_reason == "address is on the suppression list"
    assert await _messages(ids["Alpha Clinic"]) == []


async def test_unknown_country_parks_with_a_reason() -> None:
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid)
    async with async_session_maker() as s:
        from app.models.medical_tourism import Provider
        await s.execute(update(Provider).where(Provider.tenant_id == tid).values(country="ZZ"))
        await s.commit()
    out = await ENGINE.run(tid, now=NOW)
    assert out["parked"] == 1 and out["composed"] == 0
    row = await _clinic_row(ids["Alpha Clinic"])
    assert row.stage == OutreachStage.NEEDS_PERSON and row.handoff_reason == "clinic country has no known time zone"


async def test_an_onboarder_invitation_in_the_last_14_days_skips_and_defers() -> None:
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid, ("Alpha Clinic", "Beta Clinic"))
    oid = ids["Alpha Clinic"]
    async with async_session_maker() as s:
        from app.models.medical_tourism import Provider
        alpha = (await s.execute(select(Provider).where(Provider.tenant_id == tid, Provider.name == "Alpha Clinic"))).scalar_one()
        beta = (await s.execute(select(Provider).where(Provider.tenant_id == tid, Provider.name == "Beta Clinic"))).scalar_one()
        invited = NOW - timedelta(days=3)
        s.add(AuditLog(tenant_id=tid, actor_type="agent", action=INVITE_ACTION, entity_type="medical_tourism_provider", entity_id=alpha.id, result="success", created_at=invited))
        s.add(AuditLog(tenant_id=tid, actor_type="agent", action=INVITE_ACTION, entity_type="medical_tourism_provider", entity_id=beta.id, result="success",
                       created_at=NOW - timedelta(days=20)))  # too old to matter
        await s.commit()
    out = await ENGINE.run(tid, now=NOW)
    assert out["skipped"] == 1 and out["composed"] == 1
    row = await _clinic_row(oid)
    assert row.stage == OutreachStage.READY and _utc(row.next_action_at) == invited + timedelta(days=14)
    assert await _messages(oid) == []


async def test_checker_failure_stores_a_blocked_message_and_parks() -> None:
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid)
    async with async_session_maker() as s:  # a clinic name that makes the draft contain a banned claim
        from app.models.medical_tourism import Provider
        await s.execute(update(Provider).where(Provider.tenant_id == tid).values(name="Guaranteed Clinic"))
        await s.commit()
    out = await ENGINE.run(tid, now=NOW)
    assert out == {"campaigns": 1, "composed": 0, "blocked": 1, "skipped": 0, "parked": 0}  # counted once, as blocked
    [m] = await _messages(ids["Alpha Clinic"])
    assert m.status == OutreachMessageStatus.BLOCKED and m.blocked_reason and "guarantee" in m.blocked_reason.lower()
    assert "Guaranteed Clinic" in m.body and _utc(m.scheduled_for) == NOW and m.schedule_day == NOW.date()
    row = await _clinic_row(ids["Alpha Clinic"])
    assert row.stage == OutreachStage.NEEDS_PERSON and row.handoff_reason == m.blocked_reason and row.emails_sent == 0


async def test_a_failing_clinic_does_not_stop_the_others(monkeypatch) -> None:
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid, ("Alpha Clinic", "Boom Clinic", "Gamma Clinic"))
    real = clinic_outreach_engine.compose

    def flaky(kind, clinic, *a, **k):
        if clinic.name == "Boom Clinic":
            raise RuntimeError("boom info@boom.example")
        return real(kind, clinic, *a, **k)

    monkeypatch.setattr(clinic_outreach_engine, "compose", flaky)
    out = await ENGINE.run(tid, now=NOW)
    assert out == {"campaigns": 1, "composed": 2, "blocked": 0, "skipped": 1, "parked": 0}
    assert (await _clinic_row(ids["Boom Clinic"])).stage == OutreachStage.READY


async def test_three_failing_runs_park_the_clinic_and_a_success_resets_the_count(monkeypatch) -> None:
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid, ("Boom Clinic", "Fine Clinic"))
    real = clinic_outreach_engine.compose
    broken = {"on": True}

    def flaky(kind, clinic, *a, **k):
        if clinic.name == "Boom Clinic" and broken["on"]:
            raise RuntimeError("boom")
        return real(kind, clinic, *a, **k)

    monkeypatch.setattr(clinic_outreach_engine, "compose", flaky)
    oid = ids["Boom Clinic"]
    # two failures, then a success: the count resets
    for _ in range(2):
        assert (await ENGINE.run(tid, now=NOW))["parked"] == 0
    broken["on"] = False
    assert (await ENGINE.run(tid, now=NOW))["composed"] == 1  # Boom's intro (Fine's was drafted in the first run)
    assert (await _clinic_row(oid)).stage == OutreachStage.INTRO_SENT
    # follow-up 1 now fails twice (still not parked: the earlier two were reset), the third failing run parks it
    broken["on"] = True
    later = NOW + timedelta(days=4)  # a Friday: inside the send window
    for _ in range(2):
        assert (await ENGINE.run(tid, now=later))["parked"] == 0
        assert (await _clinic_row(oid)).stage == OutreachStage.INTRO_SENT
    out = await ENGINE.run(tid, now=later)
    assert out["parked"] == 1
    row = await _clinic_row(oid)
    assert row.stage == OutreachStage.NEEDS_PERSON and row.handoff_reason == "the outreach engine could not process this clinic"
    async with async_session_maker() as s:
        errors = list((await s.execute(select(AuditLog).where(AuditLog.action == "outreach.engine_clinic_error", AuditLog.entity_id == oid))).scalars())
    assert errors and all("boom" not in str(r.input_summary).lower() and "@" not in str(r.input_summary) for r in errors)


async def test_audit_run_row_has_counts_and_no_personal_data() -> None:
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid, ("Alpha Clinic", "Beta Clinic"))
    await ENGINE.run(tid, now=NOW)
    async with async_session_maker() as s:
        rows = list((await s.execute(select(AuditLog).where(AuditLog.tenant_id == tid, AuditLog.action == "outreach.engine_run"))).scalars())
    assert len(rows) == 1
    r = rows[0]
    assert r.entity_id == cid and r.input_summary == {"composed": 2, "blocked": 0, "skipped": 0, "parked": 0}
    text = str(r.input_summary)
    assert "@" not in text and "Clinic" not in text


async def test_tenant_isolation() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    cid_a, ids_a = await _campaign(a, ("Alpha Clinic",))
    cid_b, ids_b = await _campaign(b, ("Beta Clinic",))
    out = await ENGINE.run(a, now=NOW)
    assert out["campaigns"] == 1 and out["composed"] == 1
    assert len(await _messages(tid=a)) == 1 and await _messages(tid=b) == []
    assert (await _clinic_row(ids_b["Beta Clinic"])).stage == OutreachStage.READY


async def test_no_send_path_is_called(monkeypatch) -> None:
    calls: list[str] = []
    targets = list(SEND_PATHS) + [("app.communications.factory", "get_clinic_channel")]
    for module, dotted in targets:
        target, attr = _resolve(module, dotted)

        def _boom(*a, _n=f"{module}.{dotted}", **k):
            calls.append(_n)
            raise AssertionError(f"send path called by the engine: {_n}")
        monkeypatch.setattr(target, attr, _boom)
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid, ("Alpha Clinic", "Beta Clinic"))
    day = NOW
    for _ in range(14):
        await ENGINE.run(tid, now=day)
        day += timedelta(days=1)
    assert calls == [] and len(await _messages(tid=tid)) == 6


# --- fix round 1 ------------------------------------------------------------------------------------------------------------------------


async def test_hourly_runs_never_exceed_the_daily_cap() -> None:
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid, tuple(f"Clinic{c} Clinic" for c in "ABCDEFGHIJKLMNOPQRST"))
    for h in range(7):  # 10:00 to 16:00 in Istanbul; from 17:00 the window is closed and slots move to the next day
        await ENGINE.run(tid, now=NOW + timedelta(hours=h))
    msgs = await _messages(tid=tid)
    assert len(msgs) == 5 and {m.schedule_day for m in msgs} == {NOW.date()}  # warm-up day 0 allows 5, however often the engine runs


async def test_clinics_in_zones_that_fall_on_different_utc_days_use_each_days_allowance() -> None:
    tid = uuid.uuid4()
    late = NOW.replace(hour=20)  # Monday 20:00 UTC: Mexico City is mid-afternoon, Seoul's next window opens Tuesday 00:00 UTC
    cid, ids = await _campaign(tid, ("Alpha Clinic", "Beta Clinic"), country="MX")
    async with async_session_maker() as s:
        await s.execute(update(Provider).where(Provider.tenant_id == tid, Provider.name == "Beta Clinic").values(country="KR"))
        await s.commit()
    await _set_cfg(cid, limits={"daily_cap": 1, "ramp_start": 1})
    out = await ENGINE.run(tid, now=late)
    assert out["composed"] == 2
    days = sorted(m.schedule_day for m in await _messages(tid=tid))
    assert days == [late.date(), late.date() + timedelta(days=1)]


async def test_a_full_day_skips_that_clinic_but_not_the_ones_after_it() -> None:
    tid = uuid.uuid4()
    late = NOW.replace(hour=20)
    cid, ids = await _campaign(tid, ("Alpha Clinic", "Beta Clinic", "Gamma Clinic"), country="MX")
    async with async_session_maker() as s:  # order by id decides who goes first; make the last clinic the one in the other zone
        rows = (await s.execute(select(ClinicOutreach).where(ClinicOutreach.campaign_id == cid).order_by(ClinicOutreach.id))).scalars().all()
        last = rows[-1]
        await s.execute(update(Provider).where(Provider.id == last.provider_id).values(country="KR"))
        await s.commit()
    await _set_cfg(cid, limits={"daily_cap": 1, "ramp_start": 1})
    out = await ENGINE.run(tid, now=late)
    assert out["composed"] == 2 and out["skipped"] == 1  # one MX clinic (day full for the other MX one) and the KR clinic on the next day
    rows = [await _clinic_row(i) for i in ids.values()]
    waiting = [r for r in rows if r.stage == OutreachStage.READY]
    assert len(waiting) == 1 and waiting[0].next_action_at is None  # untouched


async def test_failures_are_counted_as_consecutive_across_every_kind_of_success(monkeypatch) -> None:
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid, ("Boom Clinic",))
    oid = ids["Boom Clinic"]
    monkeypatch.setattr(clinic_outreach_engine, "compose", lambda kind, clinic, *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    await ENGINE.run(tid, now=NOW)
    await ENGINE.run(tid, now=NOW)
    async with async_session_maker() as s:  # an Onboarder invitation makes the next run a skip (a success that composes nothing)
        row = await _clinic_row(oid)
        s.add(AuditLog(tenant_id=tid, actor_type="agent", action=INVITE_ACTION, entity_type="p", entity_id=row.provider_id, result="success", created_at=NOW))
        await s.commit()
    assert (await ENGINE.run(tid, now=NOW + timedelta(hours=1)))["skipped"] == 1
    out = await ENGINE.run(tid, now=NOW + timedelta(days=14, hours=1))  # one failure, 14 days later: not three in a row
    assert out["parked"] == 0 and (await _clinic_row(oid)).stage == OutreachStage.READY


async def test_a_followup_2_clinic_that_keeps_failing_is_parked(monkeypatch) -> None:
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid)
    oid = ids["Alpha Clinic"]
    await _set_stage(oid, OutreachStage.FOLLOWUP_2, NOW - timedelta(hours=1))
    async def failing(self, tenant_id, campaign_id, outreach_id, *a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(ClinicOutreachEngine, "_process", failing)
    for _ in range(2):
        assert (await ENGINE.run(tid, now=NOW))["parked"] == 0
    assert (await ENGINE.run(tid, now=NOW))["parked"] == 1
    row = await _clinic_row(oid)
    assert row.stage == OutreachStage.NEEDS_PERSON and row.handoff_reason == "the outreach engine could not process this clinic"


async def _set_stage(outreach_id, stage, next_action_at):
    async with async_session_maker() as s:
        await s.execute(update(ClinicOutreach).where(ClinicOutreach.id == outreach_id).values(stage=stage, next_action_at=next_action_at))
        await s.commit()


async def test_follow_ups_and_no_response_come_before_more_intros() -> None:
    tid = uuid.uuid4()
    names = tuple(f"Intro{i:02d} Clinic" for i in range(40))
    cid, ids = await _campaign(tid, names + ("Late Clinic", "Done Clinic"))
    await _set_stage(ids["Late Clinic"], OutreachStage.INTRO_SENT, NOW - timedelta(days=3))
    await _set_stage(ids["Done Clinic"], OutreachStage.FOLLOWUP_2, NOW - timedelta(days=1))
    async with async_session_maker() as s:  # the overdue follow-up has its intro on record
        o = await _clinic_row(ids["Late Clinic"])
        s.add(ClinicOutreachMessage(tenant_id=tid, outreach_id=o.id, direction="OUT", kind="intro", status="QUEUED_SHADOW", subject="s", body="b",
                                    blocks_version=0, scheduled_for=NOW - timedelta(days=7), schedule_day=(NOW - timedelta(days=7)).date()))
        await s.commit()
    out = await ENGINE.run(tid, now=NOW)
    assert out["composed"] == 5  # the cap still holds
    assert (await _clinic_row(ids["Done Clinic"])).stage == OutreachStage.NO_RESPONSE  # a transition, never blocked by the cap
    assert (await _clinic_row(ids["Late Clinic"])).stage == OutreachStage.FOLLOWUP_1  # drafted before any of the 40 intros
    assert len([m for m in await _messages(tid=tid) if m.kind == "intro" and _utc(m.scheduled_for) == NOW]) == 4


async def test_wording_edited_between_the_check_and_the_read_is_not_used(monkeypatch) -> None:
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid)
    tid2 = uuid.uuid4()
    await _campaign(tid2)
    from app.services.clinic_outreach_service import ClinicOutreachService
    real = ClinicOutreachService.get_blocks
    reads = []

    async def edited_after_first_read(self, tenant_id):
        reads.append(1)
        view = await real(self, tenant_id)
        # the owner saves an edit right after the first read: any later read is an unapproved draft with a new version
        return view if len(reads) == 1 else {**view, "status": "DRAFT", "version": 7}

    monkeypatch.setattr(ClinicOutreachService, "get_blocks", edited_after_first_read)
    out = await ENGINE.run(tid, now=NOW)
    assert len(reads) == 1  # approval and wording come from one read
    assert out["composed"] == 1 and [m.blocks_version for m in await _messages(tid=tid)] == [0]
    # and a read that is not approved makes nothing
    async def unapproved(self, tenant_id):
        return {**await real(self, tenant_id), "status": "DRAFT"}

    monkeypatch.setattr(ClinicOutreachService, "get_blocks", unapproved)
    assert (await ENGINE.run(tid2, now=NOW))["skipped"] == 1 and await _messages(tid=tid2) == []


async def test_a_clinic_without_an_address_is_parked() -> None:
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid)
    async with async_session_maker() as s:
        await s.execute(update(ClinicOutreach).where(ClinicOutreach.id == ids["Alpha Clinic"]).values(contact_email=None))
        await s.commit()
    out = await ENGINE.run(tid, now=NOW)
    assert out["parked"] == 1 and (await _clinic_row(ids["Alpha Clinic"])).handoff_reason == "clinic has no email address on file"


async def test_no_send_window_before_the_horizon_leaves_the_clinic_for_later() -> None:
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid)
    saturday = datetime(2026, 11, 7, 7, 0, tzinfo=UTC)  # weekdays only: the next window opens Monday, past the 24 hour horizon
    out = await ENGINE.run(tid, now=saturday)
    assert out["composed"] == 0 and out["skipped"] == 1
    row = await _clinic_row(ids["Alpha Clinic"])
    assert row.stage == OutreachStage.READY and row.next_action_at is None


async def test_a_clinic_at_its_maximum_drafts_becomes_no_response() -> None:
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid)
    await ENGINE.run(tid, now=NOW)
    await _set_cfg(cid, limits={"max_emails_per_clinic": 1})
    out = await ENGINE.run(tid, now=NOW + timedelta(days=4))
    assert out["composed"] == 0
    assert (await _clinic_row(ids["Alpha Clinic"])).stage == OutreachStage.NO_RESPONSE and len(await _messages(ids["Alpha Clinic"])) == 1


async def test_a_duplicate_insert_is_reported_as_already_done_and_leaves_the_transaction_usable() -> None:
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid)
    oid = ids["Alpha Clinic"]

    def message():
        return ClinicOutreachMessage(tenant_id=tid, outreach_id=oid, direction="OUT", kind="intro", status="QUEUED_SHADOW", subject="s", body="b",
                                     blocks_version=0, scheduled_for=NOW, schedule_day=NOW.date())

    async with async_session_maker() as s:
        assert await ClinicOutreachEngine._insert(s, message()) is True
        assert await ClinicOutreachEngine._insert(s, message()) is False  # same clinic, kind and UTC day
        s.add(AuditLog(tenant_id=tid, actor_type="system", action="x", result="success"))  # the outer transaction still works
        await s.commit()
    assert len(await _messages(oid)) == 1


# --- fix round 2 ------------------------------------------------------------------------------------------------------------------------


async def test_a_follow_up_due_later_in_the_horizon_is_not_starved_by_ready_clinics() -> None:
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid, tuple(f"Intro{i:02d} Clinic" for i in range(12)) + ("Late Clinic",))
    await _set_cfg(cid, limits={"daily_cap": 1, "ramp_start": 1})
    await _set_stage(ids["Late Clinic"], OutreachStage.INTRO_SENT, NOW + timedelta(hours=2))
    async with async_session_maker() as s:
        o = await _clinic_row(ids["Late Clinic"])
        s.add(ClinicOutreachMessage(tenant_id=tid, outreach_id=o.id, direction="OUT", kind="intro", status="QUEUED_SHADOW", subject="s", body="b",
                                    blocks_version=0, scheduled_for=NOW - timedelta(days=4), schedule_day=(NOW - timedelta(days=4)).date()))
        await s.commit()
    await ENGINE.run(tid, now=NOW)  # the single slot of the day goes to the follow-up (due in 2 hours), not to an intro
    assert (await _clinic_row(ids["Late Clinic"])).stage == OutreachStage.FOLLOWUP_1
    assert [m.kind for m in await _messages(tid=tid) if _utc(m.scheduled_for) >= NOW] == ["followup_1"]


async def test_unreadable_wording_makes_nothing(monkeypatch) -> None:
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid)
    from app.services.clinic_outreach_service import ClinicOutreachService

    async def broken(self, tenant_id):
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(ClinicOutreachService, "get_blocks", broken)
    out = await ENGINE.run(tid, now=NOW)
    assert out == {"campaigns": 1, "composed": 0, "blocked": 0, "skipped": 1, "parked": 0} and await _messages(tid=tid) == []


async def test_a_full_day_neither_clears_nor_extends_a_failure_streak(monkeypatch) -> None:
    tid = uuid.uuid4()
    cid, ids = await _campaign(tid, ("Boom Clinic", "Fine Clinic"))
    boom = ids["Boom Clinic"]
    real = clinic_outreach_engine.compose
    broken = {"on": True}

    def flaky(kind, clinic, *a, **k):
        if clinic.name == "Boom Clinic" and broken["on"]:
            raise RuntimeError("boom")
        return real(kind, clinic, *a, **k)

    async def pending() -> int:
        async with async_session_maker() as s:
            return await ClinicOutreachEngine._failures_since_success(s, tid, boom)

    monkeypatch.setattr(clinic_outreach_engine, "compose", flaky)
    await _set_stage(ids["Fine Clinic"], OutreachStage.NO_EMAIL, None)  # keep the second clinic out of the first two runs
    for _ in range(2):
        await ENGINE.run(tid, now=NOW)
    assert await pending() == 2
    await _set_cfg(cid, limits={"daily_cap": 1, "ramp_start": 1})
    async with async_session_maker() as s:  # the day already holds a draft for another clinic: Boom's day is full
        s.add(ClinicOutreachMessage(tenant_id=tid, outreach_id=ids["Fine Clinic"], direction="OUT", kind="intro", status="QUEUED_SHADOW", subject="s", body="b",
                                    blocks_version=0, scheduled_for=NOW, schedule_day=NOW.date()))
        await s.commit()
    out = await ENGINE.run(tid, now=NOW)
    assert out["skipped"] == 1 and await pending() == 2  # unchanged: not cleared, not extended
    await _set_cfg(cid, limits={})
    assert (await ENGINE.run(tid, now=NOW))["parked"] == 1  # the third real failure still parks it
