"""One-form setup for a Medical Tourism business: it turns the module on, builds the catalogue, saves the profile, and reports a launch checklist computed from what is
really recorded plus drafts that are marked as drafts. SQLite + in-process HTTP (not a deployed service)."""
from __future__ import annotations

import pytest

from app.models.rbac import Permission, Role, role_has_permission
from tests.test_business_builder_api import _seed_registry
from tests.test_business_journey_api import _register
from tests.test_halla_integration import _h, halla  # noqa: F401

BASE = "/api/v1/business-setup"
MT = "/api/v1/medical-tourism"

FORM = {"business_name": "Atlas Care Connect", "patient_markets": ["GB", "United States"], "destinations": ["India", "TR"], "procedures": ["Dental implants", "Hair transplant"],
        "business_model": "both"}


async def _reg(client, name, email):
    """Register an organisation; the platform registry rows (the test DB has no migration seed) are inserted once and re-seeding is harmless."""
    try:
        await _seed_registry()
    except Exception:  # noqa: BLE001 - already seeded
        pass
    return await _register(client, name, email)


async def test_setup_turns_the_module_on_builds_the_catalogue_and_saves_the_profile(client, halla) -> None:  # noqa: F811
    token = await _reg(client, "Setup Co", "setupco@example.com")
    assert (await client.get(f"{BASE}/medical-tourism", headers=_h(token))).json() == {"configured": False}
    assert (await client.get(f"{MT}/mode", headers=_h(token))).json()["pilot_console"] is False

    r = await client.post(f"{BASE}/medical-tourism", json=FORM, headers=_h(token))
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["configured"] is True
    assert got["profile"]["business_name"] == "Atlas Care Connect" and got["profile"]["patient_markets"] == ["GB", "US"] and got["profile"]["destinations"] == ["IN", "TR"]
    assert sorted(got["profile"]["procedures"]) == ["Dental implants", "Hair transplant"]
    assert (await client.get(f"{MT}/mode", headers=_h(token))).json()["pilot_console"] is True  # the module is on: the console appears
    procs = (await client.get(f"{MT}/procedures", headers=_h(token))).json()["procedures"]
    assert sorted(p["name"] for p in procs) == ["Dental implants", "Hair transplant"]


async def test_running_setup_again_updates_the_profile_and_never_duplicates_anything(client, halla) -> None:  # noqa: F811
    token = await _reg(client, "Again Co", "againco@example.com")
    await client.post(f"{BASE}/medical-tourism", json=FORM, headers=_h(token))
    again = await client.post(f"{BASE}/medical-tourism", json={**FORM, "business_name": "Atlas Care Renamed", "procedures": ["dental implants", "Knee replacement"]}, headers=_h(token))
    assert again.status_code == 200 and again.json()["profile"]["business_name"] == "Atlas Care Renamed"
    names = sorted(p["name"] for p in (await client.get(f"{MT}/procedures", headers=_h(token))).json()["procedures"])
    assert names == ["Dental implants", "Hair transplant", "Knee replacement"]  # the case-insensitive repeat did not create a second "dental implants"


@pytest.mark.parametrize("change,code", [
    ({"business_name": " "}, "business_name_required"),
    ({"business_model": "magic"}, "business_model_required"),
    ({"procedures": []}, "procedures_required"),
    ({"procedures": ["x"]}, "procedure_invalid"),
    ({"patient_markets": []}, "patient_markets_required"),
    ({"destinations": ["Atlantis"]}, "destinations_required"),
    ({"destinations": "India"}, "destinations_required"),
])
async def test_the_form_is_validated_and_nothing_is_created_on_a_bad_answer(client, halla, change, code) -> None:  # noqa: F811
    token = await _reg(client, f"Bad {code}", f"bad{code.replace('_', '')}@example.com")
    r = await client.post(f"{BASE}/medical-tourism", json={**FORM, **change}, headers=_h(token))
    assert r.status_code == 422 and r.json()["detail"]["error"] == code
    assert (await client.get(f"{BASE}/medical-tourism", headers=_h(token))).json() == {"configured": False}
    assert (await client.get(f"{MT}/mode", headers=_h(token))).json()["pilot_console"] is False


async def test_the_checklist_is_computed_from_what_is_really_recorded(client, halla) -> None:  # noqa: F811
    token = await _reg(client, "Check Co", "checkco@example.com")
    first = (await client.post(f"{BASE}/medical-tourism", json=FORM, headers=_h(token))).json()
    status = {i["key"]: i["status"] for i in first["checklist"]}
    assert status["profile"] == "DONE" and status["procedures"] == "DONE"
    assert status["providers"] == "TODO" and status["verified_provider"] == "NEEDS_INFORMATION" and status["offerings"] == "TODO" and status["halla"] == "TODO"
    assert status["consent_wording"] == "NEEDS_APPROVAL" and status["agreements_signed"] == "NEEDS_APPROVAL"
    assert first["ready_to_launch"] is False and first["open_items"] >= 8

    # a real clinic, a verified credential, and an offering move the real items
    p = (await client.post(f"{MT}/providers", json={"name": "Real Clinic", "country": "IN", "idempotency_key": "setup-p"}, headers=_h(token))).json()["provider"]
    c = (await client.post(f"{MT}/providers/{p['id']}/credentials", json={"credential_type": "Licence"}, headers=_h(token))).json()["credential"]
    await client.post(f"{MT}/providers/{p['id']}/credentials/{c['id']}/verify", headers=_h(token))
    assert (await client.put(f"{MT}/providers/{p['id']}/verification", json={"status": "VERIFIED"}, headers=_h(token))).status_code == 200
    proc = (await client.get(f"{MT}/procedures", headers=_h(token))).json()["procedures"][0]
    await client.post(f"{MT}/offerings", json={"provider_id": p["id"], "procedure_id": proc["id"]}, headers=_h(token))
    status = {i["key"]: i["status"] for i in (await client.get(f"{BASE}/medical-tourism", headers=_h(token))).json()["checklist"]}
    assert status["providers"] == "DONE" and status["verified_provider"] == "DONE" and status["offerings"] == "DONE"
    assert status["halla"] == "TODO" and status["agreements_signed"] == "NEEDS_APPROVAL"  # what Klaros cannot see stays open


async def test_only_the_owner_can_confirm_what_klaros_cannot_see_and_each_confirmation_is_reversible(client, halla) -> None:  # noqa: F811
    token = await _reg(client, "Confirm Co", "confirmco@example.com")
    assert (await client.post(f"{BASE}/medical-tourism/confirm", json={"key": "agreements_signed", "confirmed": True}, headers=_h(token))).json()["detail"]["error"] == "not_set_up"
    await client.post(f"{BASE}/medical-tourism", json=FORM, headers=_h(token))
    ok = (await client.post(f"{BASE}/medical-tourism/confirm", json={"key": "agreements_signed", "confirmed": True}, headers=_h(token))).json()
    assert {i["key"]: i["status"] for i in ok["checklist"]}["agreements_signed"] == "DONE"
    back = (await client.post(f"{BASE}/medical-tourism/confirm", json={"key": "agreements_signed", "confirmed": False}, headers=_h(token))).json()
    assert {i["key"]: i["status"] for i in back["checklist"]}["agreements_signed"] == "NEEDS_APPROVAL"
    bad = await client.post(f"{BASE}/medical-tourism/confirm", json={"key": "provider_is_great", "confirmed": True}, headers=_h(token))
    assert bad.status_code == 422 and bad.json()["detail"]["error"] == "unknown_confirmation"
    assert (await client.post(f"{BASE}/medical-tourism/confirm", json={"key": "domain_owned"}, headers=_h(token))).status_code == 422  # a real true/false is required


async def test_the_drafts_are_marked_as_drafts_and_invent_no_clinic_price_or_claim(client, halla) -> None:  # noqa: F811
    token = await _reg(client, "Drafts Co", "draftsco@example.com")
    drafts = (await client.post(f"{BASE}/medical-tourism", json=FORM, headers=_h(token))).json()["drafts"]
    assert set(drafts) == {"intake_questions", "qualification_rule", "pipeline", "consent_settings", "marketing_topics", "commercial_model"}
    assert all(d["status"] == "DRAFT" and d["needs"] == "owner approval" for d in drafts.values())
    assert "share_with_selected_provider" in drafts["consent_settings"]["scopes"]
    text = str(drafts).lower()
    for forbidden in ("guarantee", "best clinic", "cheapest", "%", "$", "partner of", "accredited"):
        assert forbidden not in text
    assert drafts["marketing_topics"]["items"] and all("what to ask a clinic" in t for t in drafts["marketing_topics"]["items"])
    assert "Real Clinic" not in text and "signed agreements" in drafts["commercial_model"]["note"]


async def test_setup_is_per_organisation_and_needs_the_manage_permission(client, halla) -> None:  # noqa: F811
    a = await _reg(client, "Setup A", "setupa@example.com")
    b = await _reg(client, "Setup B", "setupb@example.com")
    await client.post(f"{BASE}/medical-tourism", json=FORM, headers=_h(a))
    assert (await client.get(f"{BASE}/medical-tourism", headers=_h(b))).json() == {"configured": False}
    assert (await client.post(f"{BASE}/medical-tourism", json=FORM)).status_code in (401, 403)
    assert role_has_permission(Role.OWNER, Permission.MANAGE_BUSINESS_JOURNEY) and not role_has_permission(Role.STAFF, Permission.MANAGE_BUSINESS_JOURNEY)


async def test_only_business_types_that_really_exist_are_offered(client, halla) -> None:  # noqa: F811
    token = await _reg(client, "Types Co", "typesco@example.com")
    types = (await client.get(f"{BASE}/types", headers=_h(token))).json()["types"]
    assert [(t["key"], t["available"]) for t in types] == [("medical_tourism", True)]


async def test_the_setup_answers_become_the_active_blueprint_so_the_website_builder_works(client, halla) -> None:  # noqa: F811
    """Found on the live site: the one-form setup left the owner with no Blueprint, so 'Generate website' failed with 'requires an ACTIVE Business Blueprint'."""
    token = await _reg(client, "Blueprint Co", "blueprintco@example.com")
    assert (await client.post("/api/v1/websites/generate", headers=_h(token))).status_code == 409  # nothing yet
    await client.post(f"{BASE}/medical-tourism", json=FORM, headers=_h(token))
    made = await client.post("/api/v1/websites/generate", headers=_h(token))
    assert made.status_code in (200, 201), made.text
    assert "Atlas Care Connect" in made.text
    again = await client.post(f"{BASE}/medical-tourism", json={**FORM, "business_name": "Renamed Co"}, headers=_h(token))
    assert again.status_code == 200  # running setup again never fails on the existing Blueprint


async def test_setup_never_overwrites_a_blueprint_the_owner_already_has(client, halla) -> None:  # noqa: F811
    from app.db.session import async_session_maker
    from app.models.business_blueprint import BlueprintSectionKey
    from app.services.business_blueprint_service import BusinessBlueprintService

    token = await _reg(client, "Has Blueprint Co", "hasbp@example.com")
    tid = (await client.get("/api/v1/users/me", headers=_h(token))).json()["tenant_id"]
    import uuid as _uuid

    svc = BusinessBlueprintService(async_session_maker)
    bp = await svc.get_or_create_draft(_uuid.UUID(tid), created_by=None)
    await svc.update_section(_uuid.UUID(tid), bp.id, section_key=BlueprintSectionKey.IDENTITY.value, data={"business_name": "Owner Chosen Name"}, updated_by=None)
    await client.post(f"{BASE}/medical-tourism", json=FORM, headers=_h(token))
    sections = {s.section_key: s.data for s in await svc.list_sections(_uuid.UUID(tid), bp.id)}
    assert sections[BlueprintSectionKey.IDENTITY.value] == {"business_name": "Owner Chosen Name"}


async def test_setup_gives_the_marketing_crew_topics_from_the_owners_own_answers_and_never_overwrites_edits(client, halla) -> None:  # noqa: F811
    from app.db.session import async_session_maker
    from app.services.knowledge_service import KnowledgeService
    from app.services.mt_content_topics import TOPICS_PATH, parse_topics
    from tests.test_halla_integration import _tenant_id

    token = await _reg(client, "Topics Setup Co", "topicssetup@example.com")
    tid = await _tenant_id(client, token)
    assert (await client.post(f"{BASE}/medical-tourism", json=FORM, headers=_h(token))).status_code == 200
    knowledge = KnowledgeService(async_session_maker)
    titles = [t for t, _ in parse_topics(await knowledge.get_content(tid, TOPICS_PATH) or "")]
    assert "How Atlas Care Connect works" in titles and "Questions to ask before choosing Dental implants abroad" in titles and "Questions to ask before choosing Hair transplant abroad" in titles
    await knowledge.set_file(tid, TOPICS_PATH, "## My own topic\n\nWritten by the owner.\n", actor_id=None)
    assert (await client.post(f"{BASE}/medical-tourism", json=FORM, headers=_h(token))).status_code == 200  # setup run again
    assert parse_topics(await knowledge.get_content(tid, TOPICS_PATH) or "") == [("My own topic", "Written by the owner.")]
