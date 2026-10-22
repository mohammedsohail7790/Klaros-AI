"""Clinic outreach Phase 2 end to end through the real app: approve the wording, start shadow, run the engine tool day by day on a controllable clock, review the drafts,
and prove nothing was sent. SQLite + in-process HTTP; fake search and MX check."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, update

from app.api.tool_deps import get_tool_registry
from app.api.v1.outreach import get_outreach_service
from app.db.session import async_session_maker
from app.main import app
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.clinic_outreach import ClinicOutreach, ClinicOutreachCampaign, ClinicOutreachMessage, OutreachStage
from app.models.medical_tourism import Provider
from app.models.rbac import Role
from app.services import clinic_outreach_engine
from app.services.clinic_outreach_service import ClinicOutreachService
from app.services.mt_clinic_sourcing import SearchHit
from app.tools.base import ExecutionContext
from tests.test_business_journey_api import _register
from tests.test_clinic_outreach_phase1_end_to_end import SEND_PATHS, _resolve
from tests.test_halla_integration import _h, _tenant_id
from tests.test_outreach_api import BASE, _clinic

pytestmark = pytest.mark.asyncio

START = datetime(2026, 11, 2, 7, 0, tzinfo=UTC)  # a Monday


class OneFindableSearch:
    name = "fake"

    async def search(self, query, max_results=5):
        if query.startswith("Findable Clinic "):
            return [SearchHit("Findable Clinic - Contact", "https://www.findable.example/contact", "Write to international@findable.example for a quote.")]
        return []


async def _mx_ok(domain: str) -> bool:
    return True


def _config():
    return {
        "term_sheet": {"basis": "PERCENTAGE", "min_value": "10", "max_value": "20", "payment_days": 30, "currency": "usd", "qualifying_outcome": "Treatment completed"},
        "sender": {"display_name": "Sam", "signature": "Sam, MedLuxe", "company_name": "MedLuxe", "postal_address": "1 Main St"},
        "limits": {"daily_cap": 10, "ramp_start": 1},
        "countries": ["TR", "KR"],
    }


class _Clock:
    def __init__(self, now: datetime):
        self.now = now


def _freeze(monkeypatch, clock: _Clock) -> None:
    real = datetime

    class FakeDatetime(real):
        @classmethod
        def now(cls, tz=None):
            return clock.now if tz is None else clock.now.astimezone(tz)

    monkeypatch.setattr(clinic_outreach_engine, "datetime", FakeDatetime)


async def _kr_clinic(tid, name):
    from app.models.medical_tourism import ProviderStatus
    async with async_session_maker() as s:
        p = Provider(tenant_id=tid, name=name, country="KR", city="Seoul", status=ProviderStatus.ACTIVE, verification_status="UNVERIFIED")
        s.add(p)
        await s.commit()
        return p.id


async def test_phase_2_shadow_flow_drafts_paces_reviews_audits_and_sends_nothing(client, monkeypatch) -> None:
    calls: list[str] = []
    for module, dotted in SEND_PATHS:
        target, attr = _resolve(module, dotted)

        def _boom(*a, _n=f"{module}.{dotted}", **k):
            calls.append(_n)
            raise AssertionError(f"send path called in Phase 2: {_n}")
        monkeypatch.setattr(target, attr, _boom)
    clock = _Clock(START)
    _freeze(monkeypatch, clock)

    svc = ClinicOutreachService(async_session_maker, search_factory=lambda: OneFindableSearch(), mx_exists=_mx_ok)
    app.dependency_overrides[get_outreach_service] = lambda: svc
    try:
        token = await _register(client, "Org Phase2", "phase2@example.com")
        tid = await _tenant_id(client, token)
        h = _h(token)
        findable = await _clinic(tid, "Findable Clinic")
        hand = await _clinic(tid, "Hand Entered")
        seoul = await _kr_clinic(tid, "Seoul Clinic")
        blocked = await _clinic(tid, "Suppressed Clinic")

        cid = (await client.post(f"{BASE}/campaigns", json={"name": "Phase 2", "config": _config()}, headers=h)).json()["campaign_id"]
        assert (await client.post(f"{BASE}/campaigns/{cid}/find-contacts", headers=h)).status_code == 200
        for pid, mail in ((hand, "info@hand.example"), (seoul, "info@seoul.example"), (blocked, "info@suppressed.example")):
            assert (await client.put(f"{BASE}/campaigns/{cid}/contacts/{pid}", json={"email": mail}, headers=h)).status_code == 200
        assert (await client.post(f"{BASE}/suppressions", json={"value": "suppressed.example", "kind": "domain", "reason": "asked us to stop"}, headers=h)).status_code == 200

        assert (await client.post(f"{BASE}/campaigns/{cid}/start", headers=h)).status_code == 409  # wording not approved
        assert (await client.post(f"{BASE}/blocks/approve", headers=h)).status_code == 200
        assert (await client.post(f"{BASE}/campaigns/{cid}/start", headers=h)).status_code == 200
        async with async_session_maker() as s:  # the shadow week starts on the simulated clock
            await s.execute(update(ClinicOutreachCampaign).where(ClinicOutreachCampaign.campaign_id == uuid.UUID(cid)).values(shadow_started_at=START))
            await s.commit()

        ctx = ExecutionContext(tenant_id=tid, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=Role.OWNER)
        results = []
        for day in range(30):
            clock.now = START + timedelta(days=day)
            results.append((await get_tool_registry().execute("outreach.run_engine", {}, ctx)).model_dump())

        # The warm-up cap (1 on day 0) limited the first day; every usable clinic got its drafts eventually.
        assert results[0]["composed"] == 1 and results[0]["parked"] == 1
        async with async_session_maker() as s:
            msgs = list((await s.execute(select(ClinicOutreachMessage).where(ClinicOutreachMessage.tenant_id == tid).order_by(ClinicOutreachMessage.scheduled_for))).scalars())
            rows = {r.provider_id: r for r in (await s.execute(select(ClinicOutreach).where(ClinicOutreach.tenant_id == tid))).scalars()}
            names = {p.id: p for p in (await s.execute(select(Provider).where(Provider.tenant_id == tid))).scalars()}
        by_clinic: dict[uuid.UUID, list[ClinicOutreachMessage]] = {}
        for m in msgs:
            by_clinic.setdefault(next(pid for pid, r in rows.items() if r.id == m.outreach_id), []).append(m)
        assert set(by_clinic) == {findable, hand, seoul} and blocked not in by_clinic
        utc = lambda d: d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)  # noqa: E731
        for pid, ms in by_clinic.items():
            ms.sort(key=lambda m: utc(m.scheduled_for))
            assert [m.kind for m in ms] == ["intro", "followup_1", "followup_2"] and all(m.status == "QUEUED_SHADOW" for m in ms)
            assert utc(ms[1].scheduled_for) - utc(ms[0].scheduled_for) >= timedelta(days=4)
            assert utc(ms[2].scheduled_for) - utc(ms[1].scheduled_for) >= timedelta(days=5)
            zone = ZoneInfo("Asia/Seoul" if pid == seoul else "Europe/Istanbul")
            for m in ms:  # every draft sits in the clinic's local weekday send window
                local = utc(m.scheduled_for).astimezone(zone)
                assert 9 <= local.hour < 17 and local.weekday() < 5, (names[pid].name, local)
            assert rows[pid].stage == OutreachStage.NO_RESPONSE and rows[pid].emails_sent == 0 and rows[pid].last_sent_at is None
        assert rows[blocked].stage == OutreachStage.NEEDS_PERSON and "suppression" in (rows[blocked].handoff_reason or "")
        assert all(r.emails_sent == 0 for r in rows.values())

        # The review screen's data.
        listed = (await client.get(f"{BASE}/campaigns/{cid}/messages?limit=200", headers=h)).json()
        assert listed["total"] == 9 and all("body" not in i for i in listed["items"])
        first, second = listed["items"][0]["id"], listed["items"][1]["id"]
        detail = (await client.get(f"{BASE}/campaigns/{cid}/messages/{first}", headers=h)).json()
        assert detail["body"] and "MedLuxe" in detail["body"]
        assert (await client.put(f"{BASE}/campaigns/{cid}/messages/{first}/review", json={"mark": "GOOD"}, headers=h)).status_code == 200
        assert (await client.put(f"{BASE}/campaigns/{cid}/messages/{second}/review", json={"mark": "FLAGGED", "reason": "too formal"}, headers=h)).status_code == 200
        progress = (await client.get(f"{BASE}/campaigns/{cid}/shadow-progress", headers=h)).json()
        assert (progress["shadow_messages"], progress["reviewed"], progress["flagged"], progress["blocked"]) == (9, 2, 1, 0) and progress["flagged_share"] == 0.5
        assert [n["clinic"] for n in progress["needs_you"]] == ["Suppressed Clinic"] and progress["engine_last_run_at"]

        # Audit trail: ids and counts only.
        async with async_session_maker() as s:
            audit = list((await s.execute(select(AuditLog).where(AuditLog.tenant_id == tid, AuditLog.action.like("outreach.%")))).scalars())
        actions = {a.action for a in audit}
        assert {"outreach.blocks_approved", "outreach.engine_run", "outreach.message_reviewed"} <= actions
        assert sum(1 for a in audit if a.action == "outreach.engine_run") == 30
        assert all("@" not in str(a.input_summary) and "too formal" not in str(a.input_summary) for a in audit)

        # A paused campaign makes no drafts on the next run.
        assert (await client.post(f"{BASE}/campaigns/{cid}/pause", headers=h)).status_code == 200
        clock.now = START + timedelta(days=31)
        assert (await get_tool_registry().execute("outreach.run_engine", {}, ctx)).model_dump()["composed"] == 0
        async with async_session_maker() as s:
            assert len(list((await s.execute(select(ClinicOutreachMessage).where(ClinicOutreachMessage.tenant_id == tid))).scalars())) == 9

        # A second tenant sees and gets nothing.
        other = await _register(client, "Other Org Phase2", "phase2b@example.com")
        oh = _h(other)
        assert (await client.get(f"{BASE}/campaigns/{cid}/messages", headers=oh)).status_code == 404
        assert (await client.get(f"{BASE}/campaigns/{cid}/shadow-progress", headers=oh)).status_code == 404
        other_ctx = ExecutionContext(tenant_id=await _tenant_id(client, other), actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=Role.OWNER)
        assert (await get_tool_registry().execute("outreach.run_engine", {}, other_ctx)).model_dump() == {"campaigns": 0, "composed": 0, "blocked": 0, "skipped": 0, "parked": 0, "sent": 0, "send_failed": 0, "send_held": 0}

        assert calls == []
    finally:
        app.dependency_overrides.pop(get_outreach_service, None)
