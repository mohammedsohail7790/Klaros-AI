"""The Klaros side of the AI-workforce (Halla) boundary: honest status, the context pack,
typed events, the lead AI-interaction state model, the lead board, tenant isolation and
RBAC. Halla is never live in these tests — the dev simulator is the only event source."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.asyncio

from app.integrations.workforce.context import build_context_pack  # noqa: E402
from app.integrations.workforce.events import (  # noqa: E402
    HALLA_EVENT_TYPES,
    HallaEventType,
    InvalidWorkforceEvent,
    WorkforceInboundEvent,
    derive_interaction_state,
)
from tests.test_business_builder_api import _seed_catalog  # noqa: E402
from tests.test_business_journey_api import _register, _tech_token  # noqa: E402

BASE = "/api/v1/business-builder"


def _h(t):
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture
def dev_adapter():
    from app.core.config import get_settings

    s = get_settings()
    old = s.WORKFORCE_ADAPTER
    s.WORKFORCE_ADAPTER = "dev"
    yield
    s.WORKFORCE_ADAPTER = old


async def _lead(client, token, name="Pat Ient", source="WEB"):
    r = await client.post("/api/v1/leads", json={"name": name, "source": source, "email": f"{uuid.uuid4().hex[:6]}@example.com"}, headers=_h(token))
    assert r.status_code == 201, r.text
    return (r.json().get("lead") or r.json())["id"]


async def _sim(client, token, lead_id, etype, interaction="call-1", **extra):
    return await client.post(f"{BASE}/workforce/dev/events", json={"lead_id": lead_id, "type": etype, "interaction_id": interaction, **extra}, headers=_h(token))


# --- pure ------------------------------------------------------------------------------


def test_event_vocabulary_matches_the_contract() -> None:
    assert set(HALLA_EVENT_TYPES) == {
        "halla.interaction.started", "halla.interaction.completed", "halla.lead.qualified",
        "halla.lead.escalated", "halla.appointment.requested", "halla.appointment.confirmed",
        "halla.appointment.rescheduled", "halla.appointment.cancelled",
    }


def test_interaction_state_is_the_truth_about_the_workforce_until_events_exist() -> None:
    assert derive_interaction_state("NOT_CONNECTED", []) == "NOT_CONNECTED"
    assert derive_interaction_state("ERROR", []) == "NOT_CONNECTED"
    assert derive_interaction_state("CONFIGURATION_REQUIRED", []) == "CONFIGURATION_REQUIRED"
    assert derive_interaction_state("CONNECTED", []) == "WAITING_FOR_HALLA"


def test_recorded_events_win_and_the_latest_one_decides() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ev = lambda *pairs: [(t, t0 + timedelta(minutes=i)) for i, t in enumerate(pairs)]  # noqa: E731
    assert derive_interaction_state("NOT_CONNECTED", ev("halla.interaction.started")) == "IN_PROGRESS"
    assert derive_interaction_state("NOT_CONNECTED", ev("halla.interaction.started", "halla.interaction.completed")) == "QUALIFICATION_PENDING"
    assert derive_interaction_state("CONNECTED", ev("halla.interaction.started", "halla.lead.qualified")) == "QUALIFIED"
    assert derive_interaction_state("CONNECTED", ev("halla.lead.qualified", "halla.lead.escalated")) == "ESCALATED"
    assert derive_interaction_state("CONNECTED", ev("halla.lead.qualified", "halla.appointment.requested", "halla.appointment.confirmed")) == "APPOINTMENT_CONFIRMED"
    # completing a call never un-escalates a lead (Halla may report the escalation before or after call.completed)
    assert derive_interaction_state("CONNECTED", ev("halla.lead.escalated", "halla.interaction.completed")) == "ESCALATED"
    assert derive_interaction_state("CONNECTED", ev("halla.interaction.completed", "halla.lead.escalated")) == "ESCALATED"
    assert derive_interaction_state("CONNECTED", ev("halla.lead.escalated", "halla.interaction.completed", "halla.lead.qualified")) == "QUALIFIED"
    # order is by time, not by the order they were passed in
    shuffled = [("halla.lead.qualified", t0 + timedelta(minutes=5)), ("halla.interaction.started", t0)]
    assert derive_interaction_state("CONNECTED", shuffled) == "QUALIFIED"


def test_event_validation_rejects_oversized_or_missing_fields() -> None:
    ok = WorkforceInboundEvent(type=HallaEventType.LEAD_QUALIFIED, lead_id=uuid.uuid4(), interaction_id="i1")
    ok.validate()
    with pytest.raises(InvalidWorkforceEvent):
        WorkforceInboundEvent(type=HallaEventType.LEAD_QUALIFIED, lead_id=uuid.uuid4(), interaction_id="").validate()
    with pytest.raises(InvalidWorkforceEvent):
        WorkforceInboundEvent(type=HallaEventType.LEAD_QUALIFIED, lead_id=uuid.uuid4(), interaction_id="i", summary="x" * 2001).validate()


def test_context_pack_merges_module_contributions_without_duplicates() -> None:
    pack = build_context_pack(
        {"name": "Biz", "industry": "Care", "summary": "s", "customers": "c"},
        [{"services": ["A", "B"], "markets": ["IN"], "escalation_triggers": ["Complaint", "VIP"]}, {"services": ["B", "C"]}],
    )
    assert pack.services == ["A", "B", "C"] and pack.markets == ["IN"]
    assert pack.escalation_triggers.count("A complaint") == 1 and "VIP" in pack.escalation_triggers
    assert "Service requested" in pack.qualification_fields and pack.booking_rules


# --- adapters ---------------------------------------------------------------------------


async def test_pending_adapter_setup_is_honest_and_dev_endpoint_does_not_exist(client) -> None:
    token = await _register(client, "Pending Co", "pending@example.com")
    r = await client.get(f"{BASE}/workforce/setup", headers=_h(token))
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["status"]["status"] == "NOT_CONNECTED" and s["status"]["adapter_implemented"] is False and s["status"]["mode"] == "none"
    assert s["dev_simulator"] is False and s["channels"] == []
    assert all(m["status"] == "AVAILABLE_THROUGH_HALLA" for m in s["members"]) and len(s["members"]) == 3
    steps = {x["key"]: x for x in s["steps"]}
    assert steps["connect"]["state"] == "BLOCKED" and steps["test"]["state"] == "BLOCKED" and steps["activate"]["state"] == "BLOCKED"
    assert steps["context"]["state"] == "READY"
    lead_id = await _lead(client, token)
    assert (await _sim(client, token, lead_id, "halla.lead.qualified")).status_code == 404  # no simulator unless enabled


async def test_dev_simulator_is_never_a_connection(client, dev_adapter) -> None:
    await _seed_catalog()
    token = await _register(client, "Dev Co", "devco@example.com")
    s = (await client.get(f"{BASE}/workforce/setup", headers=_h(token))).json()
    assert s["status"]["status"] == "NOT_CONNECTED" and s["status"]["mode"] == "development" and s["status"]["adapter_implemented"] is False
    assert s["dev_simulator"] is True
    assert not any(m["status"] == "ACTIVE" for m in s["members"])
    ops = (await client.get(f"{BASE}/operations", headers=_h(token))).json()
    halla = next(i for i in ops["integrations"] if i["name"] == "Halla AI")
    assert halla["state"] == "INTEGRATION_REQUIRED"
    wf = (await client.get(f"{BASE}/workforce", headers=_h(token))).json()
    assert wf["status"] == "NOT_CONNECTED" and wf["mode"] == "development"


async def test_dev_adapter_contract_methods() -> None:
    from app.integrations.workforce.contract import WorkforceAgentSpec
    from app.integrations.workforce.dev_adapter import DevWorkforceIntegration
    from app.integrations.workforce.registry import PendingWorkforceIntegration, WorkforceNotConnectedError

    tid = uuid.uuid4()
    dev = DevWorkforceIntegration()
    spec = WorkforceAgentSpec(tenant_id=tid, business_name="B", capabilities=("voice",))
    out = await dev.deploy_agent(tid, spec)
    assert out["deployed"] is False and dev.last_spec(tid) == spec and dev.last_spec(uuid.uuid4()) is None
    assert (await dev.health_check(tid)).status.value == "NOT_CONNECTED" and await dev.get_agent(tid) is None
    pending = PendingWorkforceIntegration()
    with pytest.raises(WorkforceNotConnectedError):
        await pending.deploy_agent(tid, spec)


# --- the lead loop -------------------------------------------------------------------------


async def test_lead_interaction_starts_not_connected_then_follows_recorded_events(client, dev_adapter) -> None:
    token = await _register(client, "Loop Co", "loop@example.com")
    lead_id = await _lead(client, token)
    i = (await client.get(f"{BASE}/leads/{lead_id}/interaction", headers=_h(token))).json()
    assert i["state"] == "NOT_CONNECTED" and i["label"] == "Halla not connected" and i["events"] == [] and i["summary"] is None
    assert i["next_action"]["text"] == "Make first contact"  # the generic rule for a NEW lead

    r = await _sim(client, token, lead_id, "halla.interaction.started", channel="chat")
    assert r.status_code == 201 and r.json()["state"] == "IN_PROGRESS"
    r = await _sim(client, token, lead_id, "halla.lead.qualified", summary="Wants rhinoplasty in IN, budget 4k, next month.")
    assert r.json()["state"] == "QUALIFIED"

    i = (await client.get(f"{BASE}/leads/{lead_id}/interaction", headers=_h(token))).json()
    assert [e["type"] for e in i["events"]] == ["halla.interaction.started", "halla.lead.qualified"]
    assert all(e["simulated"] for e in i["events"]) and i["summary"].startswith("Wants rhinoplasty")
    lead = (await client.get(f"/api/v1/leads/{lead_id}", headers=_h(token))).json()
    assert (lead.get("lead") or lead)["status"] == "QUALIFIED"


async def test_escalation_marks_the_lead_for_a_person_and_surfaces_everywhere(client, dev_adapter) -> None:
    token = await _register(client, "Esc Co", "esc@example.com")
    lead_id = await _lead(client, token)
    assert (await _sim(client, token, lead_id, "halla.lead.escalated", summary="Asked for a human.")).json()["state"] == "ESCALATED"
    board = (await client.get(f"{BASE}/leads", headers=_h(token))).json()
    row = board["leads"][0]
    assert row["ai_state"] == "ESCALATED" and row["next_action"]["text"] == "A person needs to contact this customer"
    ops = (await client.get(f"{BASE}/operations", headers=_h(token))).json()
    assert ops["ai"]["escalated"] == 1 and ops["ai"]["needs_person"] == 1
    assert any(a["id"] == "needs-person" for a in ops["attention"])
    assert any(a["kind"] == "ai" and "(simulated)" in a["text"] for a in ops["activity"])


async def test_duplicate_events_are_idempotent_and_bad_input_is_rejected(client, dev_adapter) -> None:
    token = await _register(client, "Idem Co", "idem@example.com")
    lead_id = await _lead(client, token)
    for _ in range(2):
        assert (await _sim(client, token, lead_id, "halla.interaction.started", interaction="c9")).status_code == 201
    i = (await client.get(f"{BASE}/leads/{lead_id}/interaction", headers=_h(token))).json()
    assert len(i["events"]) == 1
    assert (await _sim(client, token, lead_id, "halla.nonsense")).status_code == 422
    assert (await _sim(client, token, lead_id, "halla.lead.qualified", summary="x" * 2001)).status_code == 422
    assert (await _sim(client, token, lead_id, "halla.lead.qualified", interaction="")).status_code == 422
    assert (await _sim(client, token, str(uuid.uuid4()), "halla.lead.qualified")).status_code == 404


# --- tenant isolation + RBAC ----------------------------------------------------------------


async def test_tenants_cannot_see_or_write_each_others_halla_data(client, dev_adapter) -> None:
    a = await _register(client, "Tenant A", "ta@example.com")
    b = await _register(client, "Tenant B", "tb@example.com")
    lead_a = await _lead(client, a, "A Lead")
    await _sim(client, a, lead_a, "halla.lead.qualified", summary="secret A summary")
    assert (await client.get(f"{BASE}/leads/{lead_a}/interaction", headers=_h(b))).status_code == 404
    assert (await _sim(client, b, lead_a, "halla.lead.escalated")).status_code == 404  # B cannot write against A's lead
    assert (await client.get(f"{BASE}/leads", headers=_h(b))).json() == {"leads": [], "total": 0, "limit": 50, "offset": 0}
    ops_b = (await client.get(f"{BASE}/operations", headers=_h(b))).json()
    assert ops_b["ai"]["events"] == {} and "secret A summary" not in str(ops_b)
    # A is unaffected by B's attempt
    i = (await client.get(f"{BASE}/leads/{lead_a}/interaction", headers=_h(a))).json()
    assert [e["type"] for e in i["events"]] == ["halla.lead.qualified"]
    # a tenant_id in the body or query is never authority
    r = await client.post(f"{BASE}/workforce/dev/events", json={"lead_id": lead_a, "type": "halla.lead.escalated", "interaction_id": "x", "tenant_id": (await client.get("/api/v1/users/me", headers=_h(a))).json()["tenant_id"]}, headers=_h(b))
    assert r.status_code == 404


async def test_rbac_dev_events_need_manage_integrations(client, dev_adapter) -> None:
    owner = await _register(client, "Rbac Co", "rbac@example.com")
    lead_id = await _lead(client, owner)
    tech = await _tech_token(client, owner, "tech@example.com")
    assert (await _sim(client, tech, lead_id, "halla.lead.qualified")).status_code == 403
    assert (await client.get(f"{BASE}/workforce/setup")).status_code in (401, 403)


# --- lead board + workflow detail ---------------------------------------------------------------


async def test_lead_board_filters_columns_and_clamps_the_page(client) -> None:
    token = await _register(client, "Board Co", "board@example.com")
    a = await _lead(client, token, "Alpha Web", "WEB")
    await _lead(client, token, "Beta Chat", "CHAT")
    await client.patch(f"/api/v1/leads/{a}", json={"status": "QUALIFIED"}, headers=_h(token))
    board = (await client.get(f"{BASE}/leads", headers=_h(token))).json()
    assert board["total"] == 2
    row = next(r for r in board["leads"] if r["name"] == "Alpha Web")
    assert row["status"] == "QUALIFIED" and row["priority"] and row["assigned_to"] is None
    assert row["next_action"]["text"] == "Book a consultation or appointment" and row["next_action"]["route"] == f"/leads/{a}"
    assert row["ai_state"] == "NOT_CONNECTED"
    only_chat = (await client.get(f"{BASE}/leads?source=CHAT", headers=_h(token))).json()
    assert [r["name"] for r in only_chat["leads"]] == ["Beta Chat"]
    assert (await client.get(f"{BASE}/leads?status=QUALIFIED", headers=_h(token))).json()["total"] == 1
    assert (await client.get(f"{BASE}/leads?q=alph", headers=_h(token))).json()["total"] == 1
    assert (await client.get(f"{BASE}/leads?limit=1000", headers=_h(token))).json()["limit"] == 100


async def test_workflow_detail_shows_real_runs_and_is_tenant_scoped(client, event_bus) -> None:
    from app.models.event import EventType

    a = await _register(client, "WfA", "wfa@example.com")
    b = await _register(client, "WfB", "wfb@example.com")
    wf = (await client.post(f"{BASE}/workflows/starter", headers=_h(a))).json()
    await _lead(client, a)
    await event_bus.process_pending(EventType.LEAD_CREATED)
    d = (await client.get(f"{BASE}/workflows/{wf['id']}", headers=_h(a))).json()
    assert d["name"] == "New lead alert" and d["trigger_event"] == "lead.created" and d["published"] is True
    assert d["steps"] == [{"action": "notifications.create_notification"}]
    assert d["totals"]["runs"] >= 1 and d["runs"][0]["status"] == "COMPLETED"
    assert d["runs"][0]["steps"][0]["action"] == "notifications.create_notification"
    assert (await client.get(f"{BASE}/workflows/{wf['id']}", headers=_h(b))).status_code == 404
    assert (await client.get(f"{BASE}/workflows/{uuid.uuid4()}", headers=_h(a))).status_code == 404


async def test_dev_simulator_can_never_be_enabled_in_production(client, dev_adapter) -> None:
    from app.core.config import get_settings

    s = get_settings()
    old = s.ENV
    s.ENV = "production"
    try:
        token = await _register(client, "Prod Co", "prodco@example.com")
        lead_id = await _lead(client, token)
        assert (await _sim(client, token, lead_id, "halla.lead.qualified")).status_code == 404
        setup = (await client.get(f"{BASE}/workforce/setup", headers=_h(token))).json()
        assert setup["dev_simulator"] is False and setup["status"]["mode"] == "none"
    finally:
        s.ENV = old
