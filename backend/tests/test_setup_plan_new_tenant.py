"""A brand-new business with NO module (a plain service business) signs up, defines itself, and ends up with a running AI crew and an honest setup plan.
The same code path every business takes; nothing here names a business type."""

import pytest

from app.services import operating_crew as oc
from tests.test_business_journey_api import _drive_journey_to_recommendations_ready, _register
from tests.test_halla_integration import _h


@pytest.mark.asyncio
async def test_a_new_business_with_no_module_gets_its_crew_when_its_blueprint_is_confirmed(client) -> None:
    token = await _register(client, "Plain Service Co", "plainservice@example.com")
    before = (await client.get("/api/v1/operating-crew", headers=_h(token))).json()
    assert before["running"] == 0 and before["verticals"] == [] and before["total"] == len(oc.crew_for([]))

    await _drive_journey_to_recommendations_ready(client, _h(token))  # sign-up -> idea -> discovery -> blueprint confirmed -> recommendations

    after = (await client.get("/api/v1/operating-crew", headers=_h(token))).json()
    assert after["running"] == after["total"] == len(oc.crew_for([]))  # the crew started on its own, from the generic playbook
    assert not any(m["key"] == "clinic-matcher" for m in after["members"])  # and no vertical's members, since it enabled no module


@pytest.mark.asyncio
async def test_the_setup_plan_is_honest_and_activating_it_is_idempotent(client) -> None:
    token = await _register(client, "Plan Co", "planco@example.com")
    plan = (await client.get("/api/v1/setup-plan", headers=_h(token))).json()
    steps = {s["key"]: s for s in plan["steps"]}
    assert plan["crew"] == {"running": 0, "total": len(oc.crew_for([]))} and steps["crew"]["state"] == "todo"
    assert steps["receptionist"]["state"] == "todo" and steps["receptionist"]["owner"] == "you" and steps["receptionist"]["href"] == "/workforce"
    assert steps["email"]["owner"] == "klaros" and steps["email"]["state"] in ("done", "platform")  # a platform channel the owner cannot connect
    assert plan["todo"] == sum(1 for s in plan["steps"] if s["owner"] == "you" and s["state"] == "todo")

    first = await client.post("/api/v1/setup-plan/activate", headers=_h(token))
    assert first.status_code == 200 and len(first.json()["activated"]) == len(oc.crew_for([]))
    assert {s["key"]: s for s in first.json()["steps"]}["crew"]["state"] == "done"
    again = (await client.post("/api/v1/setup-plan/activate", headers=_h(token))).json()
    assert again["activated"] == [] and again["crew"]["running"] == again["crew"]["total"]


@pytest.mark.asyncio
async def test_switched_off_members_stay_off_and_the_plan_needs_a_signed_in_user(client) -> None:
    token = await _register(client, "Off Plan Co", "offplan@example.com")
    await client.put("/api/v1/operating-crew/settings", json={"disabled_members": ["growth-scout"]}, headers=_h(token))
    out = (await client.post("/api/v1/setup-plan/activate", headers=_h(token))).json()
    assert "Growth Scout" not in out["activated"] and out["crew"]["running"] == out["crew"]["total"] - 1
    assert (await client.get("/api/v1/setup-plan")).status_code == 401
