"""The outreach engine as a crew tool, the Outreach Runner crew member, and the rule that keeps the Clinic Onboarder away from clinics a live campaign is working."""

import uuid
from datetime import UTC, datetime

import pytest

from app.api.tool_deps import get_tool_registry
from app.db.session import async_session_maker
from app.models.actor import ActorType
from app.models.clinic_outreach import ClinicOutreach, ClinicOutreachCampaign, ClinicOutreachMessage, OutreachMessageKind, OutreachMode, OutreachStage
from app.models.marketing import Campaign, CampaignStatus
from app.models.organization import Organization
from app.models.rbac import Role
from app.services import operating_crew as oc
from app.tools.base import ExecutionContext
from app.tools.errors import ToolKillSwitchError, ToolPermissionError
from app.tools.policy import DEFAULT_TOOL_POLICIES, ActionPolicy
from tests.test_clinic_outreach_service import _approve_default_blocks, _clinic, _config, _svc, USER

pytestmark = pytest.mark.asyncio


def _ctx(tid, *, actor_type=ActorType.USER, role=Role.OWNER):
    return ExecutionContext(tenant_id=tid, actor_type=actor_type, actor_id=uuid.uuid4(), role=role)


async def _org(*, ai_paused=False) -> Organization:
    async with async_session_maker() as s:
        org = Organization(name=f"Outreach Co {uuid.uuid4().hex[:6]}", slug=f"outreach-{uuid.uuid4().hex}", ai_paused=ai_paused, plan="growth", billing_status="active")
        s.add(org)
        await s.commit()
        await s.refresh(org)
        return org


async def test_the_tool_runs_the_engine_for_the_calling_tenant(monkeypatch) -> None:
    seen = []

    async def fake_run(self, tenant_id, **kw):
        seen.append(tenant_id)
        return {"campaigns": 1, "composed": 2, "blocked": 0, "skipped": 0, "parked": 1}

    from app.services.clinic_outreach_engine import ClinicOutreachEngine
    monkeypatch.setattr(ClinicOutreachEngine, "run", fake_run)
    org = await _org()
    out = await get_tool_registry().execute("outreach.run_engine", {}, _ctx(org.id))
    assert seen == [org.id]
    assert out.model_dump() == {"campaigns": 1, "composed": 2, "blocked": 0, "skipped": 0, "parked": 1, "sent": 0, "send_failed": 0, "send_held": 0}


async def test_the_tool_is_blocked_by_the_kill_switch_through_the_registry() -> None:
    org = await _org(ai_paused=True)
    with pytest.raises(ToolKillSwitchError):
        await get_tool_registry().execute("outreach.run_engine", {}, _ctx(org.id, actor_type=ActorType.WORKFLOW))


async def test_the_tool_has_an_auto_policy_and_the_right_permission() -> None:
    assert DEFAULT_TOOL_POLICIES["outreach.run_engine"] == ActionPolicy.AUTO
    org = await _org()
    with pytest.raises(ToolPermissionError):
        await get_tool_registry().execute("outreach.run_engine", {}, _ctx(org.id, role=Role.READ_ONLY))


async def test_outreach_runner_is_in_the_mt_crew_with_a_direct_action_and_no_other_tools() -> None:
    members = {m.key: m for m in oc.crew_for([oc.MEDICAL_TOURISM])}
    m = members["outreach-runner"]
    assert m.tools == ("outreach.run_engine",) and m.schedule_action == ("outreach.run_engine", {}) and m.schedule_time == "06:00"
    assert "outreach-runner" not in {x.key for x in oc.crew_for([])}
    assert m.schedule_hourly and "live" in m.purpose.lower()


async def _clinic_in_campaign(tid, *, mode):
    pid = await _clinic(tid, "Gamma Clinic", email="info@gamma.example")
    await _approve_default_blocks(tid)
    cid = await _svc().create_campaign(tid, USER, name="Turkey", config=_config(("TR",)), provider_ids=None)
    async with async_session_maker() as s:
        from sqlalchemy import update
        await s.execute(update(ClinicOutreachCampaign).where(ClinicOutreachCampaign.campaign_id == cid).values(mode=mode))
        await s.execute(update(Campaign).where(Campaign.id == cid).values(status=CampaignStatus.ACTIVE))
        await s.commit()
    return cid, pid


async def _invite(tid, pid):
    from app.tools.builtin.mt_referral_tools import InviteClinic, InviteClinicInput  # the approval gate is tested elsewhere; this exercises the tool's own refusals

    return await InviteClinic().execute(InviteClinicInput(provider_id=pid), _ctx(tid))


async def test_invite_clinic_refuses_a_clinic_in_a_live_campaign() -> None:
    org = await _org()
    _, pid = await _clinic_in_campaign(org.id, mode=OutreachMode.LIVE)
    out = await _invite(org.id, pid)
    assert out.invited is False and out.reason == "this clinic is in an active outreach campaign"


async def test_invite_clinic_is_unaffected_in_shadow_mode() -> None:
    org = await _org()
    _, pid = await _clinic_in_campaign(org.id, mode=OutreachMode.SHADOW)
    out = await _invite(org.id, pid)
    assert out.reason != "this clinic is in an active outreach campaign"  # it proceeds to its own later checks (e.g. no real e-mail provider in tests)


async def test_invite_clinic_refuses_a_clinic_with_a_sent_outreach_message() -> None:
    org = await _org()
    cid, pid = await _clinic_in_campaign(org.id, mode=OutreachMode.SHADOW)
    async with async_session_maker() as s:
        from sqlalchemy import select
        row = (await s.execute(select(ClinicOutreach).where(ClinicOutreach.campaign_id == cid, ClinicOutreach.provider_id == pid))).scalar_one()
        now = datetime(2026, 11, 2, 7, 0, tzinfo=UTC)
        s.add(ClinicOutreachMessage(tenant_id=org.id, outreach_id=row.id, kind=OutreachMessageKind.INTRO, status="SENT", subject="s", body="b", blocks_version=0, scheduled_for=now, schedule_day=now.date()))
        await s.commit()
    out = await _invite(org.id, pid)
    assert out.invited is False and out.reason == "this clinic is in an active outreach campaign"
