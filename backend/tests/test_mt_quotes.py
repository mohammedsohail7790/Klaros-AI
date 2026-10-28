"""Clinic quotes: recorded per enquiry, compared by price (never by commission), with the commission disclosed and its amounts kept for managers. SQLite + in-process HTTP."""
from __future__ import annotations

import pytest

from tests.test_halla_integration import _h, halla  # noqa: F401
from tests.test_mt_referrals import BASE, _gated, _patient, _provider


async def _terms(client, token, provider_id, **body):
    r = await client.put(f"{BASE}/providers/{provider_id}/terms", json=body, headers=_h(token))
    assert r.status_code == 200, r.text


def _quote(provider_id, amount="1000", **over):
    return {"provider_id": provider_id, "amount": amount, "currency": "USD", "includes": "Surgery, 2 nights, transfers", **over}


@pytest.mark.asyncio
async def test_quotes_are_ordered_by_price_not_commission_and_disclose_the_commission(client, halla) -> None:  # noqa: F811
    token, _ = await _gated(client, "Quotes Co", "quotes@example.com")
    lead = await _patient(client, token)
    cheap = await _provider(client, token, "Cheap Clinic")
    pricey = await _provider(client, token, "Pricey Clinic")
    none = await _provider(client, token, "No Terms Clinic")
    await _terms(client, token, cheap["id"], commission_basis="PERCENTAGE", commission_percentage="5", currency="USD")  # pays us LESS but quotes the lowest price
    await _terms(client, token, pricey["id"], commission_basis="PERCENTAGE", commission_percentage="20", currency="USD")

    for provider, amount in ((pricey, "4000"), (none, "3000"), (cheap, "2500")):
        r = await client.post(f"{BASE}/leads/{lead}/quotes", json=_quote(provider["id"], amount), headers=_h(token))
        assert r.status_code == 201, r.text

    body = (await client.get(f"{BASE}/leads/{lead}/quotes", headers=_h(token))).json()
    assert [q["provider_name"] for q in body["quotes"]] == ["Cheap Clinic", "No Terms Clinic", "Pricey Clinic"]  # price order; the 20% clinic is NOT first
    assert body["quotes"][0]["lowest_in_currency"] and not body["quotes"][1]["lowest_in_currency"]
    assert "not ordered by commission" in body["order_note"]
    by_name = {q["provider_name"]: q for q in body["quotes"]}
    assert by_name["Cheap Clinic"]["disclosure"] == "We receive a commission from this clinic."
    assert by_name["No Terms Clinic"]["disclosure"] == "We have no commission agreement with this clinic."
    # an owner (a manager) also sees the amounts
    assert by_name["Pricey Clinic"]["commission"]["estimated"] == "800.00" and by_name["Pricey Clinic"]["commission"]["percentage"] == "20.00"
    assert by_name["No Terms Clinic"]["commission"]["agreed"] is False


@pytest.mark.asyncio
async def test_a_newer_quote_from_the_same_clinic_replaces_the_earlier_one(client, halla) -> None:  # noqa: F811
    token, _ = await _gated(client, "Quotes Two Co", "quotes2@example.com")
    lead = await _patient(client, token)
    clinic = await _provider(client, token, "Repeat Clinic")
    await client.post(f"{BASE}/leads/{lead}/quotes", json=_quote(clinic["id"], "5000"), headers=_h(token))
    r = await client.post(f"{BASE}/leads/{lead}/quotes", json=_quote(clinic["id"], "4500"), headers=_h(token))
    quotes = r.json()["quotes"]
    assert [q["amount"] for q in quotes] == ["4500.00"]  # the 5000 quote is kept as superseded, not listed

    withdrawn = await client.post(f"{BASE}/quotes/{quotes[0]['id']}/withdraw", headers=_h(token))
    assert withdrawn.status_code == 200 and withdrawn.json()["quotes"] == []


@pytest.mark.asyncio
async def test_a_quote_must_say_what_it_includes_and_have_a_sensible_price(client, halla) -> None:  # noqa: F811
    token, _ = await _gated(client, "Quotes Three Co", "quotes3@example.com")
    lead = await _patient(client, token)
    clinic = await _provider(client, token, "Strict Clinic")
    for bad, code in (({"includes": ""}, "includes_required"), ({"amount": "0"}, "invalid_amount"), ({"amount": "abc"}, "invalid_amount"), ({"currency": "DOLLARS"}, "currency_required"), ({"valid_until": "soon"}, "invalid_valid_until")):
        r = await client.post(f"{BASE}/leads/{lead}/quotes", json=_quote(clinic["id"], **bad), headers=_h(token))
        assert r.status_code == 422 and r.json()["detail"]["error"] == code, (bad, r.text)
    assert (await client.post(f"{BASE}/leads/{lead}/quotes", json={"amount": "100", "currency": "USD", "includes": "x"}, headers=_h(token))).json()["detail"]["error"] == "provider_required"


@pytest.mark.asyncio
async def test_quotes_are_private_to_their_business_and_need_a_login(client, halla) -> None:  # noqa: F811
    a_token, _ = await _gated(client, "Quotes A", "qa@example.com")
    b_token, _ = await _gated(client, "Quotes B", "qb@example.com")
    lead = await _patient(client, a_token)
    clinic = await _provider(client, a_token, "A Clinic")
    await client.post(f"{BASE}/leads/{lead}/quotes", json=_quote(clinic["id"], "1000"), headers=_h(a_token))
    assert (await client.get(f"{BASE}/leads/{lead}/quotes", headers=_h(b_token))).status_code == 404  # B cannot see A's enquiry at all
    assert (await client.get(f"{BASE}/leads/{lead}/quotes")).status_code == 401


@pytest.mark.asyncio
async def test_different_currencies_are_never_mixed_and_an_expired_quote_is_marked(client, halla) -> None:  # noqa: F811
    token, _ = await _gated(client, "Quotes Four Co", "quotes4@example.com")
    lead = await _patient(client, token)
    usd = await _provider(client, token, "Dollar Clinic")
    eur = await _provider(client, token, "Euro Clinic")
    await client.post(f"{BASE}/leads/{lead}/quotes", json=_quote(usd["id"], "3000"), headers=_h(token))
    await client.post(f"{BASE}/leads/{lead}/quotes", json=_quote(eur["id"], "2000", currency="EUR", valid_until="2020-01-01"), headers=_h(token))
    body = (await client.get(f"{BASE}/leads/{lead}/quotes", headers=_h(token))).json()
    assert body["currencies"] == ["EUR", "USD"]
    assert all(q["lowest_in_currency"] for q in body["quotes"])  # each is the lowest in its own currency
    assert {q["provider_name"]: q["expired"] for q in body["quotes"]} == {"Dollar Clinic": False, "Euro Clinic": True}
