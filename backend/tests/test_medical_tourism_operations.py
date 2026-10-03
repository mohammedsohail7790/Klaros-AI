"""Medical Tourism operations: provider matching and the lead operating context.
Everything is derived from the existing domain tables; matching must be explainable,
deterministic, tenant-scoped, and must never invent a provider capability."""

import uuid

import pytest

pytestmark = pytest.mark.asyncio

from tests.test_business_builder_api import _seed_registry  # noqa: E402
from tests.test_business_journey_api import _register, _tech_token  # noqa: E402


def _lead_id(body: dict) -> str:
    return (body.get("lead") or body)["id"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _mt(client, token, path, body):
    r = await client.post(f"/api/v1/medical-tourism{path}", json=body, headers=_h(token))
    assert r.status_code == 200, r.text
    return r.json()


async def _seed(client, token):
    rhino = (await _mt(client, token, "/procedures", {"name": "Rhinoplasty", "category": "Aesthetic", "idempotency_key": "p-rhino"}))["procedure"]
    cardiac = (await _mt(client, token, "/procedures", {"name": "Cardiac bypass", "category": "Cardiac", "idempotency_key": "p-card"}))["procedure"]
    apex = (await _mt(client, token, "/providers", {"name": "Apex Hospital", "country": "IN", "city": "Delhi", "idempotency_key": "v-apex"}))["provider"]
    lotus = (await _mt(client, token, "/providers", {"name": "Lotus Clinic", "country": "TR", "city": "Istanbul", "idempotency_key": "v-lotus"}))["provider"]
    heart = (await _mt(client, token, "/providers", {"name": "Heart Institute", "country": "IN", "idempotency_key": "v-heart"}))["provider"]
    await _mt(client, token, "/offerings", {"provider_id": apex["id"], "procedure_id": rhino["id"], "estimated_price": "3200", "currency": "USD"})
    await _mt(client, token, "/offerings", {"provider_id": lotus["id"], "procedure_id": rhino["id"]})
    await _mt(client, token, "/offerings", {"provider_id": heart["id"], "procedure_id": cardiac["id"]})
    return {"rhino": rhino, "cardiac": cardiac, "apex": apex, "lotus": lotus, "heart": heart}


async def _patient_lead(client, token, tenant_id, **patient):
    lead = await client.post(
        "/api/v1/leads",
        json={"name": "Test Patient", "source": "WEB", "email": f"{uuid.uuid4().hex[:6]}@example.com",
              "description": patient.pop("description", None)},
        headers=_h(token),
    )
    assert lead.status_code in (200, 201), lead.text
    lead_id = _lead_id(lead.json())
    from app.db.session import async_session_maker
    from app.services.medical_tourism_service import MedicalTourismService

    await MedicalTourismService(async_session_maker).create_patient_lead(uuid.UUID(tenant_id), uuid.UUID(lead_id), **patient)
    return lead_id


async def _tenant(client, token):
    return (await client.get("/api/v1/users/me", headers=_h(token))).json()["tenant_id"]


async def test_matching_uses_real_offerings_and_explains_each_match(client) -> None:
    token = await _register(client, "Match Co", "match@example.com")
    ids = await _seed(client, token)
    tid = await _tenant(client, token)
    lead_id = await _patient_lead(client, token, tid, procedure_id=uuid.UUID(ids["rhino"]["id"]), preferred_destination_country="IN")
    r = await client.get(f"/api/v1/medical-tourism/leads/{lead_id}/provider-matches", headers=_h(token))
    assert r.status_code == 200, r.text
    body = r.json()
    names = [m["name"] for m in body["matches"]]
    assert names == ["Apex Hospital", "Lotus Clinic"]  # Heart Institute offers a different procedure -> never matched
    apex, lotus = body["matches"]
    assert apex["fit"] == "STRONG" and lotus["fit"] == "PARTIAL"
    assert any("Offers Rhinoplasty" in x and "3200" in x for x in apex["reasons"])
    assert any("Located in IN" in x for x in apex["reasons"])
    assert any("not the preferred IN" in x for x in lotus["reasons"])
    assert body["basis"]["procedure_source"] == "stated" and body["basis"]["procedures"] == ["Rhinoplasty"]
    assert body["none_reason"] is None


async def test_matching_infers_the_treatment_from_the_enquiry_text_and_says_so(client) -> None:
    token = await _register(client, "Infer Co", "infer@example.com")
    await _seed(client, token)
    tid = await _tenant(client, token)
    lead_id = await _patient_lead(client, token, tid, description="I would like a quote for rhinoplasty in India")
    body = (await client.get(f"/api/v1/medical-tourism/leads/{lead_id}/provider-matches", headers=_h(token))).json()
    assert body["basis"]["procedures"] == ["Rhinoplasty"]
    assert "inferred" in body["basis"]["procedure_source"]
    assert [m["name"] for m in body["matches"]][0] in ("Apex Hospital", "Lotus Clinic")


async def test_no_match_is_explained_never_invented(client) -> None:
    token = await _register(client, "None Co", "none@example.com")
    await _seed(client, token)
    tid = await _tenant(client, token)
    # Nothing in the enquiry maps to a configured procedure or a destination.
    lead_id = await _patient_lead(client, token, tid, description="Hello, please call me")
    body = (await client.get(f"/api/v1/medical-tourism/leads/{lead_id}/provider-matches", headers=_h(token))).json()
    assert body["matches"] == [] and "no treatment or destination" in body["none_reason"].lower()
    # A stated procedure that no active provider offers: says exactly what to add.
    orphan = (await _mt(client, token, "/procedures", {"name": "Dental implants", "idempotency_key": "p-dent"}))["procedure"]
    lead2 = await _patient_lead(client, token, tid, procedure_id=uuid.UUID(orphan["id"]))
    body2 = (await client.get(f"/api/v1/medical-tourism/leads/{lead2}/provider-matches", headers=_h(token))).json()
    assert body2["matches"] == [] and "Dental implants" in body2["none_reason"]


async def test_lead_operations_context_timeline_and_next_action(client) -> None:
    token = await _register(client, "Ops Co", "ops@example.com")
    ids = await _seed(client, token)
    tid = await _tenant(client, token)
    lead_id = await _patient_lead(client, token, tid, procedure_id=uuid.UUID(ids["rhino"]["id"]), preferred_destination_country="IN")
    ops = (await client.get(f"/api/v1/medical-tourism/leads/{lead_id}/operations", headers=_h(token))).json()
    assert ops["patient"]["treatment"] == "Rhinoplasty" and ops["patient"]["destination_country"] == "IN"
    assert ops["timeline"][0]["kind"] == "received" and "your website" in ops["timeline"][0]["text"]
    # NEW lead: the honest next step is a human one — nothing is auto-contacted.
    assert ops["next_action"]["state"] == "READY" and "contact the patient" in ops["next_action"]["text"]
    # After contact, with a match available, the next step is scheduling with the top match.
    patched = await client.patch(f"/api/v1/leads/{lead_id}", json={"status": "CONTACTED"}, headers=_h(token))
    assert patched.status_code == 200, patched.text
    ops2 = (await client.get(f"/api/v1/medical-tourism/leads/{lead_id}/operations", headers=_h(token))).json()
    assert "Apex Hospital" in ops2["next_action"]["text"]
    assert ops2["next_action"]["route"] == "/medical-tourism/consultations"
    # The status change is a real recorded action, and shows up on the timeline.
    assert any(t["kind"] == "action" and "Lead updated" in t["text"] for t in ops2["timeline"])


async def test_non_patient_lead_has_no_operations_context(client) -> None:
    token = await _register(client, "Plain Co", "plain2@example.com")
    lead = await client.post("/api/v1/leads", json={"name": "Plain", "source": "WEB", "email": "p@example.com"}, headers=_h(token))
    r = await client.get(f"/api/v1/medical-tourism/leads/{_lead_id(lead.json())}/operations", headers=_h(token))
    assert r.status_code == 404


async def test_operations_and_matching_are_tenant_scoped_and_authenticated(client) -> None:
    a = await _register(client, "Tenant A", "ta@example.com")
    b = await _register(client, "Tenant B", "tb@example.com")
    ids = await _seed(client, a)
    tid = await _tenant(client, a)
    lead_id = await _patient_lead(client, a, tid, procedure_id=uuid.UUID(ids["rhino"]["id"]))
    assert (await client.get(f"/api/v1/medical-tourism/leads/{lead_id}/operations", headers=_h(b))).status_code == 404
    assert (await client.get(f"/api/v1/medical-tourism/leads/{lead_id}/provider-matches", headers=_h(b))).status_code == 404
    assert (await client.get(f"/api/v1/medical-tourism/leads/{lead_id}/operations")).status_code == 401


async def test_lead_board_and_workforce_context_use_the_module_without_naming_it(client) -> None:
    token = await _register(client, "Board MT", "boardmt@example.com")
    ids = await _seed(client, token)
    tid = await _tenant(client, token)
    lead_id = await _patient_lead(client, token, tid, procedure_id=uuid.UUID(ids["rhino"]["id"]), preferred_destination_country="IN")
    plain = (await client.post("/api/v1/leads", json={"name": "Plain Lead", "source": "WEB", "email": "plain@example.com"}, headers=_h(token))).json()
    # the module only contributes once it is enabled for the tenant
    await _seed_registry()
    r = await client.post("/api/v1/business-builder/modules/medical_tourism/enable", headers=_h(token))
    assert r.status_code == 200, r.text
    board = (await client.get("/api/v1/business-builder/leads", headers=_h(token))).json()
    row = next(r for r in board["leads"] if r["id"] == lead_id)
    assert row["country"] == "IN" and row["service"] == "Rhinoplasty"
    assert row["next_action"]["text"] == "Contact the patient"  # NEW lead
    other = next(r for r in board["leads"] if r["id"] == (plain.get("lead") or plain)["id"])
    assert other["country"] is None and other["next_action"]["text"] == "Make first contact"

    setup = (await client.get("/api/v1/business-builder/workforce/setup", headers=_h(token))).json()
    ctx = setup["context"]
    assert "Rhinoplasty" in ctx["services"] and "Cardiac bypass" in ctx["services"]
    assert set(ctx["markets"]) == {"IN", "TR"}
    assert "Complex medical questions" in ctx["escalation_triggers"] and "A complaint" in ctx["escalation_triggers"]
    assert "Budget" in ctx["qualification_fields"]


async def test_analytics_breakdowns_count_real_enquiries(client) -> None:
    token = await _register(client, "Analytics MT", "analyticsmt@example.com")
    ids = await _seed(client, token)
    tid = await _tenant(client, token)
    for _ in range(2):
        await _patient_lead(client, token, tid, procedure_id=uuid.UUID(ids["rhino"]["id"]), preferred_destination_country="IN")
    await _patient_lead(client, token, tid, procedure_id=uuid.UUID(ids["cardiac"]["id"]), preferred_destination_country="TR")
    await _seed_registry()
    r = await client.post("/api/v1/business-builder/modules/medical_tourism/enable", headers=_h(token))
    assert r.status_code == 200, r.text
    ops = (await client.get("/api/v1/business-builder/operations", headers=_h(token))).json()
    br = {b["key"]: b["items"] for b in ops["module"]["breakdowns"]}
    assert br["top_procedures"][0] == {"label": "Rhinoplasty", "value": 2}
    assert br["top_destinations"][0] == {"label": "IN", "value": 2}
    assert any(a["id"] == "mt-awaiting-consultation" and a["count"] == 3 for a in ops["attention"])
