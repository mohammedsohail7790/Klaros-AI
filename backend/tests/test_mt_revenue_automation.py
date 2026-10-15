"""Clinic terms drive the money: a commission appears when a referral is sent, and a draft invoice to the clinic appears when a person confirms it was earned.

What must hold: nothing is assumed (no terms, no commission), a commission is potential until the clinic's outcome is evidenced, the invoice is only ever a DRAFT for a person to approve,
and one clinic's terms never touch another's."""

import pytest

from tests.test_mt_referrals import BASE, _approved, _gated, _h, _patient, _provider, _send, halla  # noqa: F401

pytestmark = pytest.mark.asyncio

TERMS = {"commission_basis": "PERCENTAGE", "commission_percentage": "15", "currency": "usd", "agreement_reference": "Signed 2026-10-01, folder A"}


async def _terms(client, token, provider_id, body):
    return await client.put(f"{BASE}/providers/{provider_id}/terms", json=body, headers=_h(token))


async def _commissions(client, token):
    return (await client.get(f"{BASE}/commissions", headers=_h(token))).json()["commissions"]


async def test_terms_are_validated_and_read_back(client, halla) -> None:  # noqa: F811
    token, _ = await _gated(client, "Terms Co", "termsco@example.com")
    p = await _provider(client, token)
    assert (await client.get(f"{BASE}/providers/{p['id']}/terms", headers=_h(token))).json()["terms"]["set"] is False
    for bad in ({"commission_basis": "PERCENTAGE", "currency": "usd"}, {"commission_basis": "PERCENTAGE", "commission_percentage": "150", "currency": "usd"},
                {"commission_basis": "FLAT", "flat_amount": "-5", "currency": "usd"}, {"commission_basis": "FLAT", "flat_amount": "100"}, {"commission_basis": "WEIRD"},
                {"commission_basis": "PERCENTAGE", "commission_percentage": "abc", "currency": "usd"}):
        assert (await _terms(client, token, p["id"], bad)).status_code == 422, bad
    ok = (await _terms(client, token, p["id"], TERMS)).json()["terms"]
    assert ok["currency"] == "USD" and ok["commission_percentage"] == "15.00" and ok["set"] is True
    assert (await client.get(f"{BASE}/providers/{p['id']}/terms", headers=_h(token))).json()["terms"]["agreement_reference"] == "Signed 2026-10-01, folder A"


async def test_no_terms_means_no_commission(client, halla) -> None:  # noqa: F811
    token, _ = await _gated(client, "NoTerms Co", "notermsco@example.com")
    p = await _provider(client, token)
    ref = await _approved(client, token, await _patient(client, token), p["id"])
    assert (await _send(client, token, ref)).status_code == 200
    assert await _commissions(client, token) == []


async def test_sending_a_referral_creates_a_pending_commission_from_the_terms(client, halla) -> None:  # noqa: F811
    token, _ = await _gated(client, "Auto Comm Co", "autocomm@example.com")
    p = await _provider(client, token)
    await _terms(client, token, p["id"], {"commission_basis": "FLAT", "flat_amount": "400", "currency": "eur"})
    ref = await _approved(client, token, await _patient(client, token), p["id"])
    await _send(client, token, ref)
    (c,) = await _commissions(client, token)
    assert (c["status"], c["basis"], c["currency"], c["flat_amount"], c["computed_amount"]) == ("PENDING", "FLAT", "EUR", "400.00", "400.00")
    await _send(client, token, ref)  # sending again is a no-op and never doubles the commission
    assert len(await _commissions(client, token)) == 1
    usd_or_eur = (await client.get(f"{BASE}/revenue", headers=_h(token))).json()["by_currency"]["EUR"]
    assert usd_or_eur["PENDING"]["count"] == 1 and usd_or_eur["CONFIRMED"]["count"] == 0  # potential, never revenue


async def test_confirming_an_earned_commission_drafts_one_invoice_to_the_clinic(client, halla) -> None:  # noqa: F811
    token, _ = await _gated(client, "Auto Inv Co", "autoinv@example.com")
    p = await _provider(client, token, name="Invoice Clinic")
    await _terms(client, token, p["id"], {"commission_basis": "FLAT", "flat_amount": "500", "currency": "usd"})
    ref = await _approved(client, token, await _patient(client, token), p["id"])
    await _send(client, token, ref)
    (c,) = await _commissions(client, token)
    assert c["invoice_id"] is None
    # the invoice only follows evidence
    assert (await client.post(f"{BASE}/commissions/{c['id']}/transition", json={"status": "CONFIRMED"}, headers=_h(token))).status_code == 409
    await client.post(f"{BASE}/referrals/{ref}/clinic-response", json={"accepted": True}, headers=_h(token))
    await client.post(f"{BASE}/referrals/{ref}/outcome", json={"outcome": "TREATMENT_COMPLETED", "evidence": "clinic email"}, headers=_h(token))
    done = (await client.post(f"{BASE}/commissions/{c['id']}/transition", json={"status": "CONFIRMED"}, headers=_h(token))).json()["commission"]
    assert done["status"] == "CONFIRMED" and done["invoice_id"]
    inv = (await client.get(f"/api/v1/invoices/{done['invoice_id']}", headers=_h(token))).json()
    inv = inv.get("invoice", inv)
    assert inv["status"] == "DRAFT" and inv["currency"] == "USD" and str(inv["total"]).startswith("500")
    customers = (await client.get("/api/v1/customers", headers=_h(token))).json()
    names = [x["name"] for x in (customers if isinstance(customers, list) else customers.get("customers", customers.get("items", [])))]
    assert names.count("Invoice Clinic") == 1  # the clinic's billing account, created once
    # a second confirmation attempt never raises another invoice
    again = (await client.post(f"{BASE}/commissions/{c['id']}/transition", json={"status": "CONFIRMED"}, headers=_h(token))).json()["commission"]
    assert again["invoice_id"] == done["invoice_id"]


async def test_a_percentage_commission_with_no_known_price_waits_for_an_amount_instead_of_inventing_one(client, halla) -> None:  # noqa: F811
    token, _ = await _gated(client, "Pct Co", "pctco@example.com")
    p = await _provider(client, token)
    await _terms(client, token, p["id"], {"commission_basis": "PERCENTAGE", "commission_percentage": "12", "currency": "usd"})
    ref = await _approved(client, token, await _patient(client, token), p["id"])
    await _send(client, token, ref)
    (c,) = await _commissions(client, token)
    assert c["computed_amount"] is None and c["commission_percentage"] == "12.00"
    await client.post(f"{BASE}/referrals/{ref}/clinic-response", json={"accepted": True}, headers=_h(token))
    await client.post(f"{BASE}/referrals/{ref}/outcome", json={"outcome": "TREATMENT_COMPLETED", "evidence": "clinic email"}, headers=_h(token))
    done = (await client.post(f"{BASE}/commissions/{c['id']}/transition", json={"status": "CONFIRMED"}, headers=_h(token))).json()["commission"]
    assert done["status"] == "CONFIRMED" and done["invoice_id"] is None  # no amount, so no invoice yet


async def test_one_clinics_terms_never_apply_to_another(client, halla) -> None:  # noqa: F811
    token, _ = await _gated(client, "Two Clinics Co", "twoclinics@example.com")
    a = await _provider(client, token, name="Clinic A")
    b = await _provider(client, token, name="Clinic B")
    await _terms(client, token, a["id"], {"commission_basis": "FLAT", "flat_amount": "100", "currency": "usd"})
    ref = await _approved(client, token, await _patient(client, token), b["id"])
    await _send(client, token, ref)
    assert await _commissions(client, token) == []


async def test_another_business_cannot_read_or_set_a_clinics_terms(client, halla) -> None:  # noqa: F811
    token, _ = await _gated(client, "Owner Co", "ownerco@example.com")
    other, _ = await _gated(client, "Other Co", "otherco@example.com")
    p = await _provider(client, token)
    await _terms(client, token, p["id"], TERMS)
    assert (await _terms(client, other, p["id"], TERMS)).status_code == 404
    assert (await client.get(f"{BASE}/providers/{p['id']}/terms", headers=_h(other))).json()["terms"]["set"] is False
