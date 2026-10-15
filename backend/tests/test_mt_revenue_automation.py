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


# ---- recording the outcome finishes the loop --------------------------------------------------------------------------------------------------------------

async def _accepted(client, token, provider_id):
    ref = await _approved(client, token, await _patient(client, token), provider_id)
    await _send(client, token, ref)
    await client.post(f"{BASE}/referrals/{ref}/clinic-response", json={"accepted": True}, headers=_h(token))
    return ref


async def test_an_outcome_with_the_treatment_price_confirms_a_percentage_commission_and_drafts_the_invoice(client, halla) -> None:  # noqa: F811
    token, _ = await _gated(client, "Outcome Pct Co", "outcomepct@example.com")
    p = await _provider(client, token, name="Pct Clinic")
    await _terms(client, token, p["id"], {"commission_basis": "PERCENTAGE", "commission_percentage": "15", "currency": "usd"})
    ref = await _accepted(client, token, p["id"])
    r = (await client.post(f"{BASE}/referrals/{ref}/outcome", json={"outcome": "TREATMENT_COMPLETED", "evidence": "clinic email", "treatment_amount": "4000", "currency": "usd"}, headers=_h(token))).json()
    assert r["commission"]["status"] == "CONFIRMED" and r["commission"]["invoice_id"]
    (c,) = await _commissions(client, token)
    assert (c["status"], c["computed_amount"]) == ("CONFIRMED", "600.00")  # 15% of 4000
    inv = (await client.get(f"/api/v1/invoices/{r['commission']['invoice_id']}", headers=_h(token))).json()
    assert (inv.get("invoice", inv))["status"] == "DRAFT"


async def test_a_flat_commission_is_confirmed_by_the_evidenced_outcome_alone(client, halla) -> None:  # noqa: F811
    token, _ = await _gated(client, "Outcome Flat Co", "outcomeflat@example.com")
    p = await _provider(client, token)
    await _terms(client, token, p["id"], {"commission_basis": "FLAT", "flat_amount": "350", "currency": "usd"})
    ref = await _accepted(client, token, p["id"])
    r = (await client.post(f"{BASE}/referrals/{ref}/outcome", json={"outcome": "TREATMENT_COMPLETED", "evidence": "clinic portal"}, headers=_h(token))).json()
    assert r["commission"]["status"] == "CONFIRMED" and r["commission"]["invoice_id"]


async def test_a_percentage_commission_without_the_price_waits_and_says_what_is_needed(client, halla) -> None:  # noqa: F811
    token, _ = await _gated(client, "Outcome Wait Co", "outcomewait@example.com")
    p = await _provider(client, token)
    await _terms(client, token, p["id"], {"commission_basis": "PERCENTAGE", "commission_percentage": "10", "currency": "usd"})
    ref = await _accepted(client, token, p["id"])
    r = (await client.post(f"{BASE}/referrals/{ref}/outcome", json={"outcome": "TREATMENT_COMPLETED", "evidence": "clinic email"}, headers=_h(token))).json()
    assert r["commission"]["status"] is None and "treatment price" in r["commission"]["note"]
    assert (await _commissions(client, token))[0]["status"] == "PENDING"
    later = (await client.post(f"{BASE}/referrals/{ref}/outcome", json={"outcome": "TREATMENT_COMPLETED", "evidence": "clinic email", "treatment_amount": "2500"}, headers=_h(token))).json()
    assert later["commission"]["status"] == "CONFIRMED" and (await _commissions(client, token))[0]["computed_amount"] == "250.00"


async def test_the_wrong_currency_or_a_non_qualifying_outcome_never_confirms(client, halla) -> None:  # noqa: F811
    token, _ = await _gated(client, "Outcome Guard Co", "outcomeguard@example.com")
    p = await _provider(client, token)
    await _terms(client, token, p["id"], {"commission_basis": "PERCENTAGE", "commission_percentage": "10", "currency": "usd"})
    ref = await _accepted(client, token, p["id"])
    bad = await client.post(f"{BASE}/referrals/{ref}/outcome", json={"outcome": "TREATMENT_COMPLETED", "evidence": "x", "treatment_amount": "1000", "currency": "eur"}, headers=_h(token))
    assert bad.status_code == 422 and bad.json()["detail"]["error"] == "currency_mismatch"
    neg = await client.post(f"{BASE}/referrals/{ref}/outcome", json={"outcome": "TREATMENT_COMPLETED", "evidence": "x", "treatment_amount": "-5"}, headers=_h(token))
    assert neg.status_code == 422
    r = await client.post(f"{BASE}/referrals/{ref}/outcome", json={"outcome": "NO_TREATMENT", "evidence": "x", "treatment_amount": "1000"}, headers=_h(token))
    assert r.status_code == 200 and r.json()["commission"]["status"] is None  # the agreement names treatment completed: anything else earns nothing
    assert (await _commissions(client, token))[0]["status"] == "PENDING"


async def test_the_agreements_overview_lists_every_clinic_with_or_without_terms(client, halla) -> None:  # noqa: F811
    token, _ = await _gated(client, "Agree Co", "agreeco@example.com")
    a = await _provider(client, token, name="Alpha Clinic")
    await _provider(client, token, name="Beta Clinic")
    await _terms(client, token, a["id"], {"commission_basis": "FLAT", "flat_amount": "300", "currency": "usd", "agreement_reference": "Folder 7"})
    ref = await _approved(client, token, await _patient(client, token), a["id"])
    await _send(client, token, ref)
    out = (await client.get(f"{BASE}/agreements", headers=_h(token))).json()
    by = {c["name"]: c for c in out["clinics"]}
    assert (out["with_terms"], out["without_terms"]) == (1, 1)
    assert by["Alpha Clinic"]["referrals_sent"] == 1 and by["Alpha Clinic"]["terms"]["agreement_reference"] == "Folder 7" and by["Beta Clinic"]["terms"]["set"] is False
    other, _ = await _gated(client, "Agree Other Co", "agreeother@example.com")
    assert (await client.get(f"{BASE}/agreements", headers=_h(other))).json()["clinics"] == []


async def test_the_crew_is_told_which_clinics_have_referrals_but_no_commission_terms(client, halla) -> None:  # noqa: F811
    import uuid

    from app.api.tool_deps import get_tool_registry
    from app.models.actor import ActorType
    from app.models.rbac import Role
    from app.tools.base import ExecutionContext
    from tests.test_halla_integration import _tenant_id

    token, _ = await _gated(client, "Crew Terms Co", "crewterms@example.com")
    tid = await _tenant_id(client, token)
    a = await _provider(client, token, name="Has Terms")
    b = await _provider(client, token, name="No Terms")
    await _terms(client, token, a["id"], {"commission_basis": "FLAT", "flat_amount": "100", "currency": "usd"})
    for pid in (a["id"], b["id"]):
        await _send(client, token, await _approved(client, token, await _patient(client, token), pid))
    out = await get_tool_registry().execute("medical_tourism.list_referral_work", {}, ExecutionContext(tenant_id=tid, actor_type=ActorType.AI, actor_id=uuid.uuid4(), role=Role.MANAGER))
    assert [c["clinic"] for c in out.clinics_without_commission_terms] == ["No Terms"]


async def test_terms_agreed_after_referrals_went_out_create_their_commissions(client, halla) -> None:  # noqa: F811
    token, _ = await _gated(client, "Late Terms Co", "lateterms@example.com")
    p = await _provider(client, token)
    refs = []
    for _ in range(2):
        r = await _approved(client, token, await _patient(client, token), p["id"])
        await _send(client, token, r)
        refs.append(r)
    assert await _commissions(client, token) == []  # no terms yet, so nothing is invented
    await _terms(client, token, p["id"], {"commission_basis": "FLAT", "flat_amount": "250", "currency": "usd"})
    assert sorted(c["status"] for c in await _commissions(client, token)) == ["PENDING", "PENDING"]
    await _terms(client, token, p["id"], {"commission_basis": "FLAT", "flat_amount": "999", "currency": "usd"})  # editing terms never doubles or rewrites existing commissions
    cs = await _commissions(client, token)
    assert len(cs) == 2 and {c["computed_amount"] for c in cs} == {"250.00"}


async def test_an_outcome_recorded_before_any_terms_still_finds_the_commission_once_terms_exist(client, halla) -> None:  # noqa: F811
    token, _ = await _gated(client, "Late Outcome Co", "lateoutcome@example.com")
    p = await _provider(client, token)
    ref = await _accepted(client, token, p["id"])
    first = (await client.post(f"{BASE}/referrals/{ref}/outcome", json={"outcome": "TREATMENT_COMPLETED", "evidence": "clinic email"}, headers=_h(token))).json()
    assert first["commission"]["commission_id"] is None and "Clinic agreements" in first["commission"]["note"]
    await _terms(client, token, p["id"], {"commission_basis": "FLAT", "flat_amount": "300", "currency": "usd"})
    again = (await client.post(f"{BASE}/referrals/{ref}/outcome", json={"outcome": "TREATMENT_COMPLETED", "evidence": "clinic email"}, headers=_h(token))).json()
    assert again["commission"]["status"] == "CONFIRMED" and again["commission"]["invoice_id"]
