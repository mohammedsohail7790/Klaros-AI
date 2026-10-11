"""Business Operations: the console's read-model (/business-builder/operations) and the
starter workflow. Everything must come from real records, empty businesses must look
empty, nothing may claim a state that is not true, and tenants must never see each other."""

import asyncio
import uuid

import pytest

pytestmark = pytest.mark.asyncio

from tests.test_business_builder_api import _seed_catalog, _seed_registry  # noqa: E402
from tests.test_business_journey_api import _register, _tech_token  # noqa: E402


def _h(t):
    return {"Authorization": f"Bearer {t}"}


async def _ops(client, token):
    r = await client.get("/api/v1/business-builder/operations", headers=_h(token))
    assert r.status_code == 200, r.text
    return r.json()


async def _lead(client, token, name="Lead One", source="WEB"):
    r = await client.post("/api/v1/leads", json={"name": name, "source": source, "email": f"{uuid.uuid4().hex[:6]}@example.com"}, headers=_h(token))
    assert r.status_code == 201, r.text
    return (r.json().get("lead") or r.json())["id"]


async def test_empty_business_looks_empty_and_claims_nothing(client) -> None:
    await _seed_catalog()
    token = await _register(client, "Empty Ops", "emptyops@example.com")
    o = await _ops(client, token)
    assert o["leads"]["total"] == 0 and o["leads"]["recent"] == [] and o["customers"] == 0
    assert o["workflows"] == [] and o["agents"] == [] and o["activity"] == [] and o["attention"] == []
    assert o["module"] == {"metrics": [], "data": [], "breakdowns": []}
    stages = {s["key"]: s for s in o["lead_pipeline"]}
    assert stages["form"]["state"] == "NOT_READY"  # no published website
    assert stages["notify"]["state"] == "CONFIGURATION_REQUIRED"  # no workflow yet
    by_name = {i["name"]: i for i in o["integrations"]}
    assert by_name["Stripe"]["state"] == "AVAILABLE"  # real adapter, not connected
    assert by_name["Google Ads"]["state"] == "PLANNED"  # stub adapter
    assert by_name["Halla AI"]["state"] == "INTEGRATION_REQUIRED" and by_name["Halla AI"]["category"] == "AI Workforce"
    assert not any(i["state"] == "CONNECTED" for i in o["integrations"])


async def test_lead_counts_funnel_and_attention_are_real(client) -> None:
    token = await _register(client, "Funnel Co", "funnel@example.com")
    ids = [await _lead(client, token, f"Lead {i}", "WEB" if i < 2 else "CHAT") for i in range(3)]
    r = await client.patch(f"/api/v1/leads/{ids[0]}", json={"status": "QUALIFIED"}, headers=_h(token))
    assert r.status_code == 200, r.text
    o = await _ops(client, token)
    assert o["leads"]["total"] == 3 and o["leads"]["new_7d"] == 3
    assert o["leads"]["by_status"] == {"NEW": 2, "QUALIFIED": 1}
    assert o["leads"]["by_source"] == {"WEB": 2, "CHAT": 1} and o["leads"]["website_enquiries"] == 2
    assert o["leads"]["qualified"] == 1 and len(o["leads"]["waiting"]) == 2
    assert {"id": "new-leads", "count": 2}.items() <= next(a for a in o["attention"] if a["id"] == "new-leads").items()
    kinds = [a["kind"] for a in o["activity"]]
    assert "lead" in kinds and all(a["text"] and a["at"] for a in o["activity"])


async def test_integration_state_is_derived_never_trusted(client) -> None:
    from app.db.session import async_session_maker
    from app.models.integration import ConnectionStatus, IntegrationConnection

    await _seed_catalog()
    token = await _register(client, "Conn Co", "conn@example.com")
    tid = uuid.UUID((await client.get("/api/v1/users/me", headers=_h(token))).json()["tenant_id"])
    async with async_session_maker() as s:
        # A STUB provider row claiming CONNECTED must still render as PLANNED; a REAL one renders CONNECTED.
        s.add(IntegrationConnection(tenant_id=tid, provider="google_ads", status=ConnectionStatus.CONNECTED))
        s.add(IntegrationConnection(tenant_id=tid, provider="stripe", status=ConnectionStatus.CONNECTED))
        await s.commit()
    by = {i["name"]: i for i in (await _ops(client, token))["integrations"]}
    assert by["Google Ads"]["state"] == "PLANNED"
    assert by["Stripe"]["state"] == "CONNECTED"
    assert by["Halla AI"]["state"] == "INTEGRATION_REQUIRED"


async def test_starter_workflow_is_real_idempotent_and_actually_runs(client, event_bus) -> None:
    token = await _register(client, "Flow Co", "flow@example.com")
    r1 = await client.post("/api/v1/business-builder/workflows/starter", headers=_h(token))
    assert r1.status_code == 200 and r1.json()["created"] is True and r1.json()["status"] == "ENABLED"
    r2 = await client.post("/api/v1/business-builder/workflows/starter", headers=_h(token))
    assert r2.json()["created"] is False and r2.json()["id"] == r1.json()["id"]
    o = await _ops(client, token)
    wf = o["workflows"][0]
    assert wf["name"] == "New lead alert" and wf["trigger_event"] == "lead.created" and wf["actions"] == ["notifications.create_notification"]
    assert wf["runs"] == 0 and wf["last_run"] is None  # never claims it ran
    assert {s["key"]: s for s in o["lead_pipeline"]}["notify"]["state"] == "READY"
    # A lead arrives -> the real engine runs the real step -> a real notification exists.
    await _lead(client, token, "Triggering Lead")
    from app.models.event import EventType

    await event_bus.process_pending(EventType.LEAD_CREATED)  # real bus delivery, as in the automation tests
    notes = (await client.get("/api/v1/notifications", headers=_h(token))).json()
    items = notes.get("notifications", notes) if isinstance(notes, dict) else notes
    note = next((n for n in items if n["title"] == "New lead received"), None)
    assert note is not None, "workflow did not execute"
    o2 = await _ops(client, token)
    assert o2["workflows"][0]["runs"] >= 1 and o2["workflows"][0]["last_run"]["status"] == "COMPLETED"


async def test_module_metrics_come_from_the_enabled_module_only(client) -> None:
    await _seed_registry()
    token = await _register(client, "Mod Co", "mod@example.com")
    assert (await _ops(client, token))["module"]["metrics"] == []
    assert (await client.post("/api/v1/business-builder/modules/medical_tourism/enable", headers=_h(token))).status_code == 200
    await client.post("/api/v1/medical-tourism/providers", json={"name": "Apex", "country": "IN", "idempotency_key": "a"}, headers=_h(token))
    o = await _ops(client, token)
    data = {d["key"]: d["count"] for d in o["module"]["data"]}
    assert data["providers"] == 1 and data["procedures"] == 0 and data["patient_leads"] == 0
    stages = {s["key"]: s for s in o["lead_pipeline"]}
    assert stages["provider_matching"]["state"] == "CONFIGURATION_REQUIRED"  # no offerings yet — says so
    assert any(a["text"] == "Provider added: Apex" for a in o["activity"])
    by = {b["key"]: b["items"] for b in o["module"]["breakdowns"]}
    assert by["providers_by_country"] == [{"label": "IN", "value": 1}]


async def test_operations_are_tenant_scoped_authenticated_and_rbac_protected(client) -> None:
    a = await _register(client, "Iso A", "isoa@example.com")
    b = await _register(client, "Iso B", "isob@example.com")
    await _lead(client, a, "Only A")
    await client.post("/api/v1/business-builder/workflows/starter", headers=_h(a))
    ob = await _ops(client, b)
    assert ob["leads"]["total"] == 0 and ob["workflows"] == [] and ob["activity"] == []
    assert (await client.get("/api/v1/business-builder/operations")).status_code == 401
    assert (await client.post("/api/v1/business-builder/workflows/starter")).status_code == 401
    tech = await _tech_token(client, a, "techops@example.com")
    assert (await client.post("/api/v1/business-builder/workflows/starter", headers=_h(tech))).status_code == 403


async def test_requirements_launch_readiness_and_integration_center_agree(client) -> None:
    """One source of truth: the same provider can never be CONNECTED in one view and not in another,
    and the lead/provider counts shown by Launch Readiness and by the operating console are the same."""
    from app.db.session import async_session_maker
    from app.models.integration import ConnectionStatus, IntegrationConnection
    from tests.test_business_builder_api import _DROPSHIP, _journey_via_fallback

    await _seed_catalog()
    token = await _register(client, "Agree Co", "agree@example.com")
    h = _h(token)
    tid = uuid.UUID((await client.get("/api/v1/users/me", headers=h)).json()["tenant_id"])
    await _journey_via_fallback(client, h, *_DROPSHIP)
    async with async_session_maker() as s:
        s.add(IntegrationConnection(tenant_id=tid, provider="stripe", status=ConnectionStatus.CONNECTED))
        s.add(IntegrationConnection(tenant_id=tid, provider="google_ads", status=ConnectionStatus.CONNECTED))  # STUB: must stay PLANNED
        await s.commit()
    await _lead(client, token, "Agreement Lead")
    overview = (await client.get("/api/v1/business-builder/overview", headers=h)).json()
    ops = await _ops(client, token)
    center = {i["provider_key"]: i["state"] for i in ops["integrations"]}
    seen = 0
    for r in overview["requirements"]:
        for p in r["providers"]:
            seen += 1
            expected = center[p["provider_key"]]
            assert p["state"] == ("NOT_CONNECTED" if expected == "AVAILABLE" else expected), p
    assert seen > 0 and center["stripe"] == "CONNECTED" and center["google_ads"] == "PLANNED"
    assert overview["operations"]["lead_count"] == ops["leads"]["total"] == 1
