"""Phone numbers for the AI receptionist (Klaros side): a new number bought through Halla with the price shown and accepted, or the business's own
existing number as the hand-off line. LABELLING: Halla is NOT contacted; every request goes to an `httpx.MockTransport` (MOCKED HTTP). These tests
prove what Klaros SENDS, how it maps Halla's answers to plain messages, and that nothing from an error body leaks. SQLite + in-process HTTP."""
from __future__ import annotations

import json

import httpx
import pytest

from tests.test_halla_integration import _h, halla  # noqa: F401
from tests.test_halla_provisioning import PLATFORM_SECRET, _ready_mt, provisioner  # noqa: F401

pytestmark = pytest.mark.asyncio

BASE = "/api/v1/business-builder"
PHONE = f"{BASE}/workforce/halla/phone"


def _err(status: int, code: str | None, text: str = "internal text that must never reach the owner") -> httpx.Response:
    return httpx.Response(status, json={"success": False, "error": text, **({"code": code} if code else {})})


async def test_status_is_translated_and_asks_halla_for_this_business_only(client, provisioner) -> None:  # noqa: F811
    token = await _ready_mt(client, "phone-status@example.com")
    r = await client.get(PHONE, headers=_h(token))
    assert r.status_code == 200, r.text
    assert r.json() == {
        "numbers": [], "business_number": None, "human_handoff": False, "can_buy": True, "monthly_cost_usd": 1.15,
        "plan": {"plan": "professional", "status": "trialing", "trial_expires_at": "2026-10-26T00:00:00Z", "trial_minutes_used": 0},
    }
    (call,) = provisioner.phone_calls
    assert call.headers["Authorization"] == f"Bearer {PLATFORM_SECRET}"
    sent = json.loads(call.content)
    assert sent["action"] == "status" and set(sent) == {"klarosTenantId", "action"}  # nothing else, and the id is the signed-in business's own
    assert PLATFORM_SECRET not in r.text


async def test_search_refuses_a_bad_area_code_without_asking_halla_and_sends_digits_only(client, provisioner) -> None:  # noqa: F811
    token = await _ready_mt(client, "phone-search@example.com")
    bad = await client.post(f"{PHONE}/search", json={"area_code": "41"}, headers=_h(token))
    assert bad.status_code == 422 and "3-digit" in bad.json()["detail"]
    assert provisioner.phone_calls == []
    ok = await client.post(f"{PHONE}/search", json={"area_code": "(415)"}, headers=_h(token))
    assert ok.status_code == 200
    assert ok.json() == {"numbers": [{"phone_number": "+14155550123", "friendly_name": "(415) 555-0123"}], "monthly_cost_usd": 1.15}
    assert json.loads(provisioner.phone_calls[0].content)["areaCode"] == "415"


async def test_buying_sends_exactly_the_chosen_number_and_the_accepted_cost(client, provisioner) -> None:  # noqa: F811
    token = await _ready_mt(client, "phone-buy@example.com")
    r = await client.post(f"{PHONE}/buy", json={"phone_number": "+14155550123", "accepted_monthly_cost_usd": 1.15}, headers=_h(token))
    assert r.status_code == 200, r.text
    assert r.json() == {"phone_number": "+14155550123", "monthly_cost_usd": 1.15, "receptionist_pinned": True}
    sent = json.loads(provisioner.phone_calls[0].content)
    assert sent["action"] == "buy" and sent["phoneNumber"] == "+14155550123" and sent["acceptedMonthlyCostUsd"] == 1.15


async def test_a_buy_with_no_accepted_cost_carries_none_so_halla_refuses_it(client, provisioner) -> None:  # noqa: F811
    token = await _ready_mt(client, "phone-nocost@example.com")
    provisioner.phone_replies["buy"] = _err(400, "cost_not_accepted")
    for body in ({"phone_number": "+14155550123"}, {"phone_number": "+14155550123", "accepted_monthly_cost_usd": "1.15"}, {"phone_number": "+14155550123", "accepted_monthly_cost_usd": True}):
        r = await client.post(f"{PHONE}/buy", json=body, headers=_h(token))
        assert r.status_code == 422 and "accept the monthly cost" in r.json()["detail"]
    assert all(json.loads(c.content)["acceptedMonthlyCostUsd"] is None for c in provisioner.phone_calls)  # never defaulted to "accepted"
    empty = await client.post(f"{PHONE}/buy", json={}, headers=_h(token))
    assert empty.status_code == 422


@pytest.mark.parametrize("status,code,expect_status,expect_text", [
    (409, "already_has_number", 409, "already has a phone number"),
    (409, "purchase_in_progress", 409, "already in progress"),
    (429, "monthly_cap_reached", 429, "monthly limit"),
    (502, "number_unavailable", 409, "just taken"),
    (503, "not_available", 503, "aren't available"),
    (409, "no_receptionist", 409, "Create your AI receptionist first"),
])
async def test_halla_answers_become_plain_messages(client, provisioner, status, code, expect_status, expect_text) -> None:  # noqa: F811
    token = await _ready_mt(client, f"phone-map-{code}@example.com")
    provisioner.phone_replies["buy"] = _err(status, code)
    r = await client.post(f"{PHONE}/buy", json={"phone_number": "+14155550123", "accepted_monthly_cost_usd": 1.15}, headers=_h(token))
    assert r.status_code == expect_status, r.text
    assert expect_text in r.json()["detail"]
    assert "internal text" not in r.text


async def test_free_text_in_an_error_body_never_reaches_the_owner(client, provisioner) -> None:  # noqa: F811
    token = await _ready_mt(client, "phone-leak@example.com")
    # no code, an invalid code, and a code that tries to carry text: all become the same generic message
    for reply in (_err(500, None, "patient Jane Doe 555-0100"), _err(502, "Jane Doe!", "patient Jane Doe 555-0100"), httpx.Response(500, text="boom")):
        provisioner.phone_replies["search"] = reply
        r = await client.post(f"{PHONE}/search", json={"area_code": "415"}, headers=_h(token))
        assert r.status_code == 502 and "Jane" not in r.text and "555-0100" not in r.text and "boom" not in r.text


async def test_the_business_number_is_stored_through_halla_and_can_be_removed(client, provisioner) -> None:  # noqa: F811
    token = await _ready_mt(client, "phone-biz@example.com")
    r = await client.put(f"{PHONE}/business-number", json={"business_number": "(415) 555-0199"}, headers=_h(token))
    assert r.status_code == 200 and r.json() == {"business_number": "(415) 555-0199", "human_handoff": True}  # Halla normalises; the fake echoes
    assert json.loads(provisioner.phone_calls[0].content)["businessNumber"] == "(415) 555-0199"
    cleared = await client.put(f"{PHONE}/business-number", json={"business_number": ""}, headers=_h(token))
    assert cleared.status_code == 200 and cleared.json() == {"business_number": None, "human_handoff": False}
    assert json.loads(provisioner.phone_calls[1].content)["businessNumber"] is None


async def test_a_number_halla_rejects_as_malformed_or_as_its_own_line_is_explained(client, provisioner) -> None:  # noqa: F811
    token = await _ready_mt(client, "phone-badbiz@example.com")
    provisioner.phone_replies["set_business_number"] = _err(400, None)
    bad = await client.put(f"{PHONE}/business-number", json={"business_number": "12345"}, headers=_h(token))
    assert bad.status_code == 422 and "country code" in bad.json()["detail"]
    provisioner.phone_replies["set_business_number"] = _err(409, "is_halla_line")
    loop = await client.put(f"{PHONE}/business-number", json={"business_number": "+14155550123"}, headers=_h(token))
    assert loop.status_code == 422 and "already one of our lines" in loop.json()["detail"]


async def test_phone_actions_need_the_manage_permission_and_the_platform_secret(client, provisioner) -> None:  # noqa: F811
    from app.core.config import get_settings
    from tests.test_business_journey_api import _tech_token

    token = await _ready_mt(client, "phone-perm@example.com")
    tech = await _tech_token(client, token, "tech-phone@example.com")
    for method, path, body in (("post", f"{PHONE}/search", {"area_code": "415"}), ("post", f"{PHONE}/buy", {"phone_number": "+14155550123", "accepted_monthly_cost_usd": 1.15}),
                               ("put", f"{PHONE}/business-number", {"business_number": "+14155550199"})):
        r = await getattr(client, method)(path, json=body, headers=_h(tech))
        assert r.status_code in (401, 403), (path, r.status_code)
    assert provisioner.phone_calls == []
    s = get_settings()
    s.HALLA_PROVISIONING_SECRET = None
    gone = await client.get(PHONE, headers=_h(token))
    assert gone.status_code == 404 and "not available" in gone.json()["detail"]
    assert provisioner.phone_calls == []
