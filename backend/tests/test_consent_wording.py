"""Owner-approved consent wording: the owner approves the exact words, a published form shows them, and the intake gate accepts exactly those versions.

Closes a real gap: until now the only way a wording could count as approved was a value written by hand into the Halla connection's metadata, which no screen or
endpoint could do, so a Medical Tourism business's public enquiry form could never open. SQLite + in-process HTTP (not a deployed service)."""
from __future__ import annotations

import pytest

from tests.test_business_journey_api import _register, _tech_token
from tests.test_consent_gate_intake import ALL, PII_EMAIL, PII_NAME, _evidence, _gated, _leads
from tests.test_halla_integration import _h, halla  # noqa: F401

pytestmark = pytest.mark.asyncio

BASE = "/api/v1/consent-wordings"
TEXT = "I agree that Gate Clinic may keep the details I enter on this form and contact me about my enquiry. I can withdraw this at any time by contacting the business."
TEXT2 = TEXT + " Health information is only collected when I choose to share it."


async def test_the_owner_sees_a_draft_and_no_approved_wording_to_begin_with(client, halla) -> None:  # noqa: F811
    token, _ = await _gated(client, "Draft Co", "draftco@example.com", wording=())
    r = await client.get(BASE, headers=_h(token))
    assert r.status_code == 200
    body = r.json()
    assert body["wordings"] == []
    assert "Draft Co" in body["suggested_text"] and body["min_length"] == 40  # a DRAFT only: nothing is approved until the owner approves it


async def test_approving_creates_numbered_immutable_versions_and_refuses_a_repeat(client, halla) -> None:  # noqa: F811
    token, _ = await _gated(client, "Ver Co", "verco@example.com", wording=())
    v1 = await client.post(BASE, json={"text": TEXT}, headers=_h(token))
    assert v1.status_code == 201 and v1.json()["version"] == "WEB-v1" and v1.json()["text"] == TEXT and v1.json()["language"] == "en"
    assert (await client.post(BASE, json={"text": TEXT}, headers=_h(token))).status_code == 409      # the same words are not a new version
    v2 = await client.post(BASE, json={"text": TEXT2, "language": "ar"}, headers=_h(token))
    assert v2.status_code == 201 and v2.json()["version"] == "WEB-v2" and v2.json()["language"] == "ar"
    listed = (await client.get(BASE, headers=_h(token))).json()["wordings"]
    assert [w["version"] for w in listed] == ["WEB-v2", "WEB-v1"]
    assert listed[1]["text"] == TEXT  # the first version still says exactly what it said
    # nothing can edit or remove a version
    assert (await client.put(f"{BASE}/WEB-v1", json={"text": TEXT2}, headers=_h(token))).status_code in (404, 405)
    assert (await client.delete(f"{BASE}/WEB-v1", headers=_h(token))).status_code in (404, 405)


@pytest.mark.parametrize("body,fragment", [
    ({"text": "too short"}, "between 40"),
    ({"text": "x" * 2001}, "between 40"),
    ({"text": TEXT + " <script>alert(1)</script>"}, "plain text"),
    ({"text": TEXT + " see javascript:alert(1)"}, "plain text"),
    ({"text": TEXT, "language": "fr"}, "English or Arabic"),
    ({}, "between 40"),
])
async def test_wording_that_is_too_short_unsafe_or_in_an_unsupported_language_is_refused(client, halla, body, fragment) -> None:  # noqa: F811
    token, _ = await _gated(client, f"Bad {fragment[:4]}", f"bad{abs(hash(str(body))) % 10**6}@example.com", wording=())
    r = await client.post(BASE, json=body, headers=_h(token))
    assert r.status_code == 422 and fragment in r.json()["detail"]
    assert (await client.get(BASE, headers=_h(token))).json()["wordings"] == []


async def test_only_someone_who_manages_integrations_can_approve(client, halla) -> None:  # noqa: F811
    token, _ = await _gated(client, "Perm Co", "permco@example.com", wording=())
    tech = await _tech_token(client, token, "tech-wording@example.com")
    assert (await client.post(BASE, json={"text": TEXT}, headers=_h(tech))).status_code in (401, 403)
    assert (await client.get(BASE, headers=_h(token))).json()["wordings"] == []


async def test_a_published_form_reads_the_latest_wording_and_stays_closed_until_there_is_one(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Pub Co", "pubco@example.com", wording=())
    closed = await client.get(f"/api/v1/public/websites/{tid}/consent")
    assert closed.status_code == 200 and closed.json() == {"required": True, "open": False}
    await client.post(BASE, json={"text": TEXT}, headers=_h(token))
    await client.post(BASE, json={"text": TEXT2}, headers=_h(token))
    now = (await client.get(f"/api/v1/public/websites/{tid}/consent")).json()
    assert now == {"required": True, "open": True, "version": "WEB-v2", "text": TEXT2, "language": "en"}


async def test_a_business_that_does_not_need_consent_is_not_asked_to_show_any(client, halla) -> None:  # noqa: F811
    from tests.test_consent_gate_intake import _plain

    token, _ = await _plain(client, "Plain Co", "plainco@example.com")
    me = (await client.get("/api/v1/users/me", headers=_h(token))).json()
    r = await client.get(f"/api/v1/public/websites/{me['tenant_id']}/consent")
    assert r.status_code == 200 and r.json() == {"required": False, "open": True}


async def test_the_public_enquiry_form_opens_once_the_owner_approves_and_files_evidence_against_that_exact_version(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Open Co", "openco@example.com", wording=())
    url = f"/api/v1/public/leads/{tid}"
    base = {"name": PII_NAME, "source": "WEB", "email": PII_EMAIL}
    shut = await client.post(url, json={**base, "consent": {"scopes": ALL, "wording_version": "WEB-v1"}})
    assert shut.status_code == 403 and shut.json()["detail"]["error"] == "public_intake_closed" and await _leads(client, token) == []

    assert (await client.post(BASE, json={"text": TEXT}, headers=_h(token))).status_code == 201
    assert (await client.post(url, json=base)).status_code == 422                                              # no consent object
    wrong = await client.post(url, json={**base, "consent": {"scopes": ALL, "wording_version": "WEB-v9"}})
    assert wrong.status_code == 422 and wrong.json()["detail"]["error"] == "wording_not_approved"             # a version nobody approved
    assert await _leads(client, token) == []

    ok = await client.post(url, json={**base, "consent": {"scopes": ["contact", "store_personal_data"], "wording_version": "WEB-v1"}})
    assert ok.status_code == 201 and ok.json()["lead_id"]
    ev = await _evidence(tid)
    assert len(ev) == 1 and ev[0].source == "web_form" and ev[0].wording_version == "WEB-v1" and set(ev[0].scopes) == {"contact", "store_personal_data"}

    # a newer version is approved: a form loaded before it still files correctly against the version it showed
    assert (await client.post(BASE, json={"text": TEXT2}, headers=_h(token))).status_code == 201
    still = await client.post(url, json={**base, "email": "second@example.com", "consent": {"scopes": ["store_personal_data"], "wording_version": "WEB-v1"}})
    assert still.status_code == 201


async def test_the_launch_checklist_counts_an_approved_wording(client, halla) -> None:  # noqa: F811
    from tests.test_business_builder_api import _seed_registry
    from tests.test_mt_setup import FORM

    try:
        await _seed_registry()  # the test database has no migration seed; re-seeding is harmless
    except Exception:  # noqa: BLE001 - already seeded
        pass
    token, _ = await _gated(client, "Check Co", "checkco@example.com", wording=())
    assert (await client.post("/api/v1/business-setup/medical-tourism", json=FORM, headers=_h(token))).status_code == 200
    item = lambda r: next(i for i in r.json()["checklist"] if i["key"] == "consent_wording")  # noqa: E731
    before = item(await client.get("/api/v1/business-setup/medical-tourism", headers=_h(token)))
    assert before["status"] == "NEEDS_APPROVAL" and before["route"] == "/setup#consent-wording"
    await client.post(BASE, json={"text": TEXT}, headers=_h(token))
    assert item(await client.get("/api/v1/business-setup/medical-tourism", headers=_h(token)))["status"] == "DONE"
