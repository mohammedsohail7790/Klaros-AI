"""The operating crew: playbooks are valid against the live tool registry and trigger schema, and provisioning creates, publishes and activates the crew once.
SQLite + in-process; no model is called."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.api.tool_deps import get_tool_registry
from app.db.session import async_session_maker, set_tenant_context
from app.models.agent import Agent, AgentStatus, AgentToolPermission
from app.models.event import EventType
from app.services import operating_crew as oc
from app.services.agent_service import AgentService, _validate_triggers
from app.tools.policy import SYSTEM_BLOCKED_TOOLS
from tests.test_consent_gate_intake import _gated
from tests.test_halla_integration import halla  # noqa: F401


def _all_members():
    return [m for crew in oc.PLAYBOOKS.values() for m in crew]


def test_every_tool_a_crew_member_uses_exists_in_the_live_registry() -> None:
    registry = get_tool_registry()
    for m in _all_members():
        for tool in m.tools:
            registry.get(tool)  # raises if the playbook names a tool that does not exist


def test_no_crew_member_is_given_a_system_blocked_tool() -> None:
    for m in _all_members():
        assert not set(m.tools) & set(SYSTEM_BLOCKED_TOOLS), m.key


def test_every_trigger_passes_the_agent_trigger_validation() -> None:
    for m in _all_members():
        triggers = oc._triggers(m)
        assert triggers, f"{m.key} would never run: it has neither an event nor a schedule"
        _validate_triggers(triggers)
        if m.event_type:
            assert m.event_type in {e.value for e in EventType}


def test_crew_keys_and_names_are_unique_and_vertical_crews_are_added_not_swapped() -> None:
    names = [m.name for m in _all_members()]
    assert len(names) == len(set(names))
    plain = {m.key for m in oc.crew_for([])}
    mt = {m.key for m in oc.crew_for([oc.MEDICAL_TOURISM])}
    assert plain < mt and "clinic-matcher" in mt and "clinic-matcher" not in plain
    assert oc.crew_for(["no_such_vertical"]) == oc.crew_for([])


@pytest.mark.asyncio
async def test_provisioning_creates_publishes_and_activates_the_crew_exactly_once(client, halla) -> None:  # noqa: F811
    _, tid = await _gated(client, "Crew Co", "crew@example.com")
    service = AgentService(async_session_maker)
    registry = get_tool_registry()

    first = await oc.provision_crew(service, registry, tid, None, [oc.MEDICAL_TOURISM])
    expected = [m.name for m in oc.crew_for([oc.MEDICAL_TOURISM])]
    assert sorted(first.created) == sorted(expected) and not first.failed and not first.skipped_tools

    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        agents = list((await s.execute(select(Agent).where(Agent.tenant_id == tid))).scalars())
        grants = list((await s.execute(select(AgentToolPermission).where(AgentToolPermission.tenant_id == tid))).scalars())
    assert len(agents) == len(expected)
    assert all(a.status == AgentStatus.ACTIVE and a.current_version_id for a in agents)
    assert all(oc.CREW_MARKER in a.purpose for a in agents)
    assert len(grants) == sum(len(m.tools) for m in oc.crew_for([oc.MEDICAL_TOURISM]))

    second = await oc.provision_crew(service, registry, tid, None, [oc.MEDICAL_TOURISM])  # idempotent
    assert second.created == [] and sorted(second.already) == sorted(expected)
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        assert len(list((await s.execute(select(Agent).where(Agent.tenant_id == tid))).scalars())) == len(expected)


@pytest.mark.asyncio
async def test_the_api_shows_the_playbook_and_provisions_it_once(client, halla) -> None:  # noqa: F811
    from tests.test_halla_integration import _h

    token, _ = await _gated(client, "Crew Api Co", "crewapi@example.com")  # not enrolled in the Medical Tourism module, so only the generic crew applies
    status = (await client.get("/api/v1/operating-crew", headers=_h(token))).json()
    assert status["running"] == 0 and status["total"] == len(oc.crew_for([])) and status["verticals"] == []
    assert any(m["key"] == "lead-responder" and m["trigger"] == "When lead.created happens" for m in status["members"])
    assert not any(m["key"] == "clinic-matcher" for m in status["members"])  # a vertical's crew never appears for a tenant without that module

    first = await client.post("/api/v1/operating-crew/provision", headers=_h(token))
    assert first.status_code == 200 and len(first.json()["created"]) == status["total"] and not first.json()["failed"]
    after = (await client.get("/api/v1/operating-crew", headers=_h(token))).json()
    assert after["running"] == after["total"]
    again = (await client.post("/api/v1/operating-crew/provision", headers=_h(token))).json()
    assert again["created"] == [] and len(again["already_running"]) == after["total"]


@pytest.mark.asyncio
async def test_provisioning_needs_the_manage_agents_permission(client, halla) -> None:  # noqa: F811
    assert (await client.post("/api/v1/operating-crew/provision")).status_code == 401


@pytest.mark.asyncio
async def test_ensure_crew_sets_up_a_business_without_a_button_and_adds_a_modules_members(client, halla) -> None:  # noqa: F811
    from tests.test_consent_gate_intake import _enable_vertical

    _, tid = await _gated(client, "Auto Crew Co", "autocrew@example.com")
    await _enable_vertical(tid)
    first = await oc.ensure_crew(async_session_maker, tid, None)
    assert first is not None and sorted(first.created) == sorted(m.name for m in oc.crew_for([oc.MEDICAL_TOURISM])) and not first.failed
    again = await oc.ensure_crew(async_session_maker, tid, None)
    assert again is not None and again.created == []  # idempotent


@pytest.mark.asyncio
async def test_a_tenant_can_switch_a_member_off_and_the_whole_crew_off(client, halla) -> None:  # noqa: F811
    from tests.test_halla_integration import _h

    token, tid = await _gated(client, "Off Crew Co", "offcrew@example.com")
    assert (await client.get("/api/v1/operating-crew", headers=_h(token))).json()["auto_provision"] is True
    await client.post("/api/v1/operating-crew/provision", headers=_h(token))
    r = await client.put("/api/v1/operating-crew/settings", json={"disabled_members": ["growth-scout"]}, headers=_h(token))
    assert r.status_code == 200 and r.json()["disabled_members"] == ["growth-scout"]
    crew = (await client.get("/api/v1/operating-crew", headers=_h(token))).json()
    scout = next(m for m in crew["members"] if m["key"] == "growth-scout")
    assert scout["switched_off"] is True and scout["running"] is False and scout["status"] == "PAUSED"
    assert crew["running"] == crew["total"] - 1
    # provisioning again never restarts it
    assert "Growth Scout" in (await client.post("/api/v1/operating-crew/provision", headers=_h(token))).json()["switched_off"]
    # switching it back on starts it
    await client.put("/api/v1/operating-crew/settings", json={"disabled_members": []}, headers=_h(token))
    assert (await client.get("/api/v1/operating-crew", headers=_h(token))).json()["running"] == crew["total"]
    # the whole crew can be left to the owner: nothing is set up automatically
    await client.put("/api/v1/operating-crew/settings", json={"auto_provision": False}, headers=_h(token))
    assert await oc.ensure_crew(async_session_maker, tid, None) is None


@pytest.mark.asyncio
async def test_unknown_members_are_refused_and_settings_need_manage_agents(client, halla) -> None:  # noqa: F811
    from tests.test_halla_integration import _h

    token, _ = await _gated(client, "Bad Crew Co", "badcrew@example.com")
    assert (await client.put("/api/v1/operating-crew/settings", json={"disabled_members": ["no-such"]}, headers=_h(token))).status_code == 422
    assert (await client.put("/api/v1/operating-crew/settings", json={})).status_code == 401


@pytest.mark.asyncio
async def test_the_activity_endpoint_reports_what_each_member_last_did_without_internal_errors(client, halla) -> None:  # noqa: F811
    from datetime import datetime, timedelta, timezone

    from app.models.agent import AgentExecution
    from tests.test_halla_integration import _h

    token, tid = await _gated(client, "Crew Activity Co", "crewactivity@example.com")
    assert (await client.get("/api/v1/operating-crew/activity")).status_code == 401
    await client.post("/api/v1/operating-crew/provision", headers=_h(token))
    service = AgentService(async_session_maker)
    agents = {a.name: a for a in await service.list_agents(tid)}
    lead = agents["Lead Responder"]
    now = datetime.now(timezone.utc)
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        common = dict(tenant_id=tid, agent_id=lead.id, agent_version_id=lead.current_version_id, trigger_source="EVENT")
        s.add(AgentExecution(**common, status="FAILED", error_message="ValidationError: secret internal text", created_at=now - timedelta(hours=3)))
        s.add(AgentExecution(**common, status="COMPLETED", final_response="Qualified the enquiry as high intent.\n  Next step: call.", completed_at=now - timedelta(hours=1), created_at=now - timedelta(hours=1)))
        s.add(AgentExecution(**common, status="FAILED", error_message="old", created_at=now - timedelta(days=20)))  # outside the window
        await s.commit()

    body = (await client.get("/api/v1/operating-crew/activity", headers=_h(token))).json()
    rows = {m["name"]: m for m in body["members"]}
    assert len(rows) == len(oc.crew_for([]))
    lr = rows["Lead Responder"]
    assert lr["runs"] == 2 and lr["failed"] == 1  # the 20-day-old run is outside the 7-day window
    assert lr["last"]["status"] == "COMPLETED" and lr["last"]["summary"] == "Qualified the enquiry as high intent. Next step: call."
    assert rows["Owner Brief"]["last"] is None and rows["Owner Brief"]["runs"] == 0

    # a member whose latest run failed gets no summary, so internal error text never reaches the page
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        s.add(AgentExecution(tenant_id=tid, agent_id=lead.id, agent_version_id=lead.current_version_id, trigger_source="EVENT", status="FAILED", error_message="ValidationError: secret internal text", created_at=now))
        await s.commit()
    body = (await client.get("/api/v1/operating-crew/activity", headers=_h(token))).json()
    last = {m["name"]: m for m in body["members"]}["Lead Responder"]["last"]
    assert last["status"] == "FAILED" and last["summary"] is None and "secret" not in str(body)
