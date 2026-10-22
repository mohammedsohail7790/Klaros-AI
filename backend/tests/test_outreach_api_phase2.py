"""Wording and shadow-review API (Phase 2): the approval round trip, per-block errors, permissions, tenant isolation, review marks, previews and progress. Nothing is sent."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.session import async_session_maker
from app.models.audit_log import AuditLog
from app.models.clinic_outreach import ClinicOutreachMessage
from tests.test_agent_api import _readonly_token
from tests.test_business_journey_api import _register
from tests.test_clinic_outreach_engine import ENGINE, NOW, _campaign, _messages
from tests.test_halla_integration import _h, _tenant_id
from tests.test_outreach_api import BASE, _service  # noqa: F401  (autouse fixture: the API uses a fake-search service)

pytestmark = pytest.mark.asyncio


async def _owner(client, email):
    token = await _register(client, f"Org {email}", email)
    return token, await _tenant_id(client, token)


async def _with_drafts(client, email, names=("Alpha Clinic", "Beta Clinic")):
    token, tid = await _owner(client, email)
    cid, ids = await _campaign(tid, names)
    await ENGINE.run(tid, now=NOW)
    return token, tid, str(cid), ids


async def test_blocks_default_save_approve_round_trip(client) -> None:
    token, _ = await _owner(client, "b1@example.com")
    got = (await client.get(f"{BASE}/blocks", headers=_h(token))).json()
    assert got["status"] == "DRAFT" and got["version"] == 0 and "opt_out_line" in got["blocks"]
    edited = {**got["blocks"], "faq_what_we_do": "We help patients from abroad find treatment, and we are paid only by clinics."}
    saved = await client.put(f"{BASE}/blocks", json={"blocks": edited}, headers=_h(token))
    assert saved.status_code == 200 and saved.json()["version"] == 1 and saved.json()["status"] == "DRAFT"
    approved = await client.post(f"{BASE}/blocks/approve", headers=_h(token))
    assert approved.status_code == 200 and approved.json()["status"] == "APPROVED" and approved.json()["approved_by"]
    again = await client.post(f"{BASE}/blocks/approve", headers=_h(token))
    assert again.status_code == 409
    reopened = await client.put(f"{BASE}/blocks", json={"blocks": edited}, headers=_h(token))
    assert reopened.json()["status"] == "DRAFT" and reopened.json()["version"] == 2


async def test_invalid_blocks_are_422_with_per_block_reasons(client) -> None:
    token, _ = await _owner(client, "b2@example.com")
    blocks = (await client.get(f"{BASE}/blocks", headers=_h(token))).json()["blocks"]
    bad = {**blocks, "faq_what_we_do": "We guarantee 10 patients per month."}
    r = await client.put(f"{BASE}/blocks", json={"blocks": bad}, headers=_h(token))
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert isinstance(detail, dict) and "faq_what_we_do" in detail and detail["faq_what_we_do"]


async def test_blocks_mutations_need_manage_permission_and_reads_need_read(client) -> None:
    owner, tid = await _owner(client, "b3@example.com")
    ro = await _readonly_token(client, tid, "ro-b3@example.com")
    assert (await client.get(f"{BASE}/blocks", headers=_h(ro))).status_code == 200
    blocks = (await client.get(f"{BASE}/blocks", headers=_h(owner))).json()["blocks"]
    assert (await client.put(f"{BASE}/blocks", json={"blocks": blocks}, headers=_h(ro))).status_code == 403
    assert (await client.post(f"{BASE}/blocks/approve", headers=_h(ro))).status_code == 403


async def test_messages_list_filters_and_pages(client) -> None:
    token, _, cid, _ = await _with_drafts(client, "m1@example.com")
    all_ = (await client.get(f"{BASE}/campaigns/{cid}/messages", headers=_h(token))).json()
    assert all_["total"] == 2 and {i["clinic"] for i in all_["items"]} == {"Alpha Clinic", "Beta Clinic"}
    assert all("body" not in i for i in all_["items"])
    page = (await client.get(f"{BASE}/campaigns/{cid}/messages?limit=1&offset=1", headers=_h(token))).json()
    assert page["total"] == 2 and len(page["items"]) == 1
    assert (await client.get(f"{BASE}/campaigns/{cid}/messages?status=BLOCKED", headers=_h(token))).json()["total"] == 0
    assert (await client.get(f"{BASE}/campaigns/{cid}/messages?status=QUEUED_SHADOW&stage=INTRO_SENT", headers=_h(token))).json()["total"] == 2
    assert (await client.get(f"{BASE}/campaigns/{cid}/messages?limit=0", headers=_h(token))).status_code == 422


async def test_message_detail_includes_the_body_only_there(client) -> None:
    token, _, cid, _ = await _with_drafts(client, "m2@example.com", ("Alpha Clinic",))
    mid = (await client.get(f"{BASE}/campaigns/{cid}/messages", headers=_h(token))).json()["items"][0]["id"]
    detail = (await client.get(f"{BASE}/campaigns/{cid}/messages/{mid}", headers=_h(token))).json()
    assert "Alpha Clinic" in detail["body"] and detail["subject"]


async def test_review_good_and_flagged_and_clear(client) -> None:
    token, tid, cid, _ = await _with_drafts(client, "r1@example.com", ("Alpha Clinic",))
    mid = (await client.get(f"{BASE}/campaigns/{cid}/messages", headers=_h(token))).json()["items"][0]["id"]
    url = f"{BASE}/campaigns/{cid}/messages/{mid}/review"
    assert (await client.put(url, json={"mark": "GOOD"}, headers=_h(token))).status_code == 200
    assert (await client.get(f"{BASE}/campaigns/{cid}/messages/{mid}", headers=_h(token))).json()["review_mark"] == "GOOD"
    assert (await client.put(url, json={"mark": "FLAGGED", "reason": "tone is too pushy"}, headers=_h(token))).status_code == 200
    d = (await client.get(f"{BASE}/campaigns/{cid}/messages/{mid}", headers=_h(token))).json()
    assert d["review_mark"] == "FLAGGED" and d["review_reason"] == "tone is too pushy"
    assert (await client.get(f"{BASE}/campaigns/{cid}/messages?review=FLAGGED", headers=_h(token))).json()["total"] == 1
    assert (await client.put(url, json={"mark": None}, headers=_h(token))).status_code == 200
    d = (await client.get(f"{BASE}/campaigns/{cid}/messages/{mid}", headers=_h(token))).json()
    assert d["review_mark"] is None and d["review_reason"] is None
    async with async_session_maker() as s:
        rows = (await s.execute(select(AuditLog).where(AuditLog.tenant_id == tid, AuditLog.action == "outreach.message_reviewed"))).scalars().all()
    assert len(rows) == 3 and all("@" not in str(r.input_summary) and "pushy" not in str(r.input_summary) for r in rows)


async def test_a_flag_without_a_reason_is_422(client) -> None:
    token, _, cid, _ = await _with_drafts(client, "r2@example.com", ("Alpha Clinic",))
    mid = (await client.get(f"{BASE}/campaigns/{cid}/messages", headers=_h(token))).json()["items"][0]["id"]
    url = f"{BASE}/campaigns/{cid}/messages/{mid}/review"
    assert (await client.put(url, json={"mark": "FLAGGED"}, headers=_h(token))).status_code == 422
    assert (await client.put(url, json={"mark": "FLAGGED", "reason": "  "}, headers=_h(token))).status_code == 422
    assert (await client.put(url, json={"mark": "MAYBE"}, headers=_h(token))).status_code == 422


async def test_preview_sequence_returns_three_messages_without_storing(client) -> None:
    token, tid = await _owner(client, "p1@example.com")
    cid, ids = await _campaign(tid, ("Alpha Clinic",))
    async with async_session_maker() as s:
        from app.models.clinic_outreach import ClinicOutreach
        pid = (await s.execute(select(ClinicOutreach.provider_id).where(ClinicOutreach.id == ids["Alpha Clinic"]))).scalar_one()
    r = await client.get(f"{BASE}/campaigns/{cid}/clinics/{pid}/preview-sequence", headers=_h(token))
    assert r.status_code == 200
    msgs = r.json()["messages"]
    assert [m["kind"] for m in msgs] == ["intro", "followup_1", "followup_2"] and all(m["problems"] == [] and "Alpha Clinic" in m["body"] for m in msgs)
    assert await _messages(ids["Alpha Clinic"]) == []


async def test_preview_refuses_when_blocks_are_not_approved(client) -> None:
    token, tid = await _owner(client, "p2@example.com")
    cid, ids = await _campaign(tid, ("Alpha Clinic",), approve=False)
    async with async_session_maker() as s:
        from app.models.clinic_outreach import ClinicOutreach
        pid = (await s.execute(select(ClinicOutreach.provider_id).where(ClinicOutreach.id == ids["Alpha Clinic"]))).scalar_one()
    assert (await client.get(f"{BASE}/campaigns/{cid}/clinics/{pid}/preview-sequence", headers=_h(token))).status_code == 409


async def test_shadow_progress_counts_and_needs_you(client) -> None:
    token, tid, cid, ids = await _with_drafts(client, "s1@example.com")
    mid = (await client.get(f"{BASE}/campaigns/{cid}/messages", headers=_h(token))).json()["items"][0]["id"]
    await client.put(f"{BASE}/campaigns/{cid}/messages/{mid}/review", json={"mark": "FLAGGED", "reason": "too long"}, headers=_h(token))
    p = (await client.get(f"{BASE}/campaigns/{cid}/shadow-progress", headers=_h(token))).json()
    assert p["shadow_messages"] == 2 and p["reviewed"] == 1 and p["flagged"] == 1 and p["flagged_share"] == 1.0 and p["blocked"] == 0
    assert p["blocks_status"] == "APPROVED" and p["engine_last_run_at"] and p["try_a_reply_tests"] == 0 and p["try_a_reply_available"] is True and p["golive_checks"]["all_ok"] is False
    assert p["needs_you"] == [] and len(p["next_sends"]) == 2 and all(n["kind"] == "followup_1" for n in p["next_sends"])


async def test_another_tenant_gets_404_everywhere(client) -> None:
    token, _, cid, _ = await _with_drafts(client, "t1@example.com", ("Alpha Clinic",))
    mid = (await client.get(f"{BASE}/campaigns/{cid}/messages", headers=_h(token))).json()["items"][0]["id"]
    other = await _register(client, "Other Org", "t1b@example.com")
    h = _h(other)
    pid = uuid.uuid4()
    for method, url, body in [
        ("get", f"{BASE}/campaigns/{cid}/messages", None),
        ("get", f"{BASE}/campaigns/{cid}/messages/{mid}", None),
        ("put", f"{BASE}/campaigns/{cid}/messages/{mid}/review", {"mark": "GOOD"}),
        ("get", f"{BASE}/campaigns/{cid}/shadow-progress", None),
    ]:
        r = await getattr(client, method)(url, headers=h, **({"json": body} if body is not None else {}))
        assert r.status_code == 404, url
    await client.post(f"{BASE}/blocks/approve", headers=h)
    assert (await client.get(f"{BASE}/campaigns/{cid}/clinics/{pid}/preview-sequence", headers=h)).status_code in (404, 409)
    async with async_session_maker() as s:
        row = (await s.execute(select(ClinicOutreachMessage).where(ClinicOutreachMessage.id == uuid.UUID(mid)))).scalar_one()
    assert row.review_mark is None


async def test_unauthenticated_requests_are_401(client) -> None:
    cid = uuid.uuid4()
    for method, url in [("get", f"{BASE}/blocks"), ("put", f"{BASE}/blocks"), ("post", f"{BASE}/blocks/approve"), ("get", f"{BASE}/campaigns/{cid}/messages"),
                        ("get", f"{BASE}/campaigns/{cid}/shadow-progress")]:
        assert (await getattr(client, method)(url)).status_code == 401, url


async def test_bad_uuid_is_422(client) -> None:
    token, _ = await _owner(client, "u1@example.com")
    for url in (f"{BASE}/campaigns/nope/messages", f"{BASE}/campaigns/{uuid.uuid4()}/messages/nope", f"{BASE}/campaigns/{uuid.uuid4()}/clinics/nope/preview-sequence"):
        assert (await client.get(url, headers=_h(token))).status_code == 422, url
