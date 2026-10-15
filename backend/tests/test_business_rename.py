"""Renaming the business updates every place the name was recorded, keeps history, protects the owner's own edits, and is the owner's alone."""

import pytest

from app.db.session import async_session_maker
from app.services.knowledge_service import KnowledgeService
from app.services.mt_content_topics import TOPICS_PATH, parse_topics
from tests.test_halla_integration import _h, _tenant_id, halla  # noqa: F401
from tests.test_mt_setup import BASE, FORM, MT, _reg

pytestmark = pytest.mark.asyncio

NAME = "/api/v1/organization/name"


async def test_a_plain_business_can_be_renamed_by_its_owner_and_validation_is_clear(client) -> None:
    token = await _reg(client, "Old Name Co", "oldname@example.com")
    for bad in ("", "x", "<b>hi</b>", "a" * 121):
        assert (await client.put(NAME, json={"name": bad}, headers=_h(token))).status_code == 422, bad
    r = await client.put(NAME, json={"name": "  New   Name  Ltd "}, headers=_h(token))
    assert r.status_code == 200 and r.json()["name"] == "New Name Ltd" and r.json()["organization"] is True and r.json()["medical_tourism_profile"] is False
    assert (await client.put(NAME, json={"name": "New Name Ltd"}, headers=_h(token))).json()["unchanged"] is True


async def test_a_medical_tourism_business_is_renamed_in_its_profile_blueprint_and_topics_with_history_kept(client, halla) -> None:  # noqa: F811
    token = await _reg(client, "Rename Setup Co", "renamesetup@example.com")
    tid = await _tenant_id(client, token)
    assert (await client.post(f"{BASE}/medical-tourism", json=FORM, headers=_h(token))).status_code == 200
    r = (await client.put(NAME, json={"name": "MedLuxe Travel"}, headers=_h(token))).json()
    assert r["organization"] and r["medical_tourism_profile"] and r["blueprint"] and r["content_topics"]
    assert (await client.get(f"{BASE}/medical-tourism", headers=_h(token))).json()["profile"]["business_name"] == "MedLuxe Travel"
    sections = (await client.get("/api/v1/business-blueprint", headers=_h(token))).json()
    ident = next(s for s in sections["sections"] if s["section_key"] == "IDENTITY")
    assert ident["data"]["business_name"] == "MedLuxe Travel" and "Atlas Care Connect" not in ident["data"]["description"] and sections["blueprint"]["version"] == 2
    titles = [t for t, _ in parse_topics(await KnowledgeService(async_session_maker).get_content(tid, TOPICS_PATH) or "")]
    assert "How MedLuxe Travel works" in titles and not any("Atlas" in t for t in titles)


async def test_an_owner_who_edited_the_topics_keeps_their_edits_on_rename(client, halla) -> None:  # noqa: F811
    token = await _reg(client, "Rename Edit Co", "renameedit@example.com")
    tid = await _tenant_id(client, token)
    await client.post(f"{BASE}/medical-tourism", json=FORM, headers=_h(token))
    await KnowledgeService(async_session_maker).set_file(tid, TOPICS_PATH, "## Mine\n\nAtlas Care Connect is great.\n", actor_id=None)
    r = (await client.put(NAME, json={"name": "MedLuxe Travel"}, headers=_h(token))).json()
    assert r["content_topics"] is False
    assert parse_topics(await KnowledgeService(async_session_maker).get_content(tid, TOPICS_PATH)) == [("Mine", "Atlas Care Connect is great.")]


async def test_only_the_owner_can_rename_and_another_business_is_untouched(client) -> None:
    owner = await _reg(client, "Rename Own Co", "renameown@example.com")
    other = await _reg(client, "Rename Other Co", "renameother@example.com")
    assert (await client.put(NAME, json={"name": "Owner New"}, headers=_h(owner))).status_code == 200
    other_tid = await _tenant_id(client, other)
    from app.models.organization import Organization
    from app.db.session import set_tenant_context

    async with async_session_maker() as s:
        await set_tenant_context(s, other_tid)
        assert (await s.get(Organization, other_tid)).name == "Rename Other Co"
