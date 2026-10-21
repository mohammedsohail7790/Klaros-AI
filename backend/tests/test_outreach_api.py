"""Clinic outreach API: permissions, tenant isolation, error mapping and the campaign round trip. SQLite + in-process HTTP; fake search and MX check, nothing is sent."""
from __future__ import annotations

import uuid

import pytest

from app.api.v1.outreach import get_outreach_service
from app.db.session import async_session_maker
from app.main import app
from app.models.medical_tourism import Provider, ProviderStatus
from app.services.clinic_outreach_service import ClinicOutreachService
from app.services.mt_clinic_sourcing import SearchHit
from tests.test_business_journey_api import _register
from tests.test_halla_integration import _h, _tenant_id
from tests.test_agent_api import _readonly_token

BASE = "/api/v1/outreach"


class FakeSearch:
    name = "fake"

    async def search(self, query, max_results=5):
        return [SearchHit("Alpha Clinic - Contact", "https://www.alpha.example/contact", "Write to international@alpha.example for a quote.")]


async def _mx_ok(domain: str) -> bool:
    return True


@pytest.fixture(autouse=True)
def _service():
    svc = ClinicOutreachService(async_session_maker, search_factory=lambda: FakeSearch(), mx_exists=_mx_ok)
    app.dependency_overrides[get_outreach_service] = lambda: svc
    yield
    app.dependency_overrides.pop(get_outreach_service, None)


def _config(**term):
    ts = {"basis": "PERCENTAGE", "min_value": "10", "max_value": "20", "payment_days": 30, "currency": "usd", "qualifying_outcome": "Treatment completed", **term}
    return {"term_sheet": ts, "sender": {"display_name": "Sam", "signature": "Sam, MedLuxe", "company_name": "MedLuxe", "postal_address": "1 Main St"}, "countries": ["TR"]}


async def _clinic(tid, name="Alpha Clinic") -> uuid.UUID:
    async with async_session_maker() as s:
        p = Provider(tenant_id=tid, name=name, country="TR", city="Istanbul", status=ProviderStatus.ACTIVE, verification_status="UNVERIFIED")
        s.add(p)
        await s.commit()
        return p.id


async def _setup(client, email):
    token = await _register(client, f"Org {email}", email)
    tid = await _tenant_id(client, token)
    pid = await _clinic(tid)
    return token, tid, pid


async def _create(client, token, **kw):
    return await client.post(f"{BASE}/campaigns", json={"name": "Turkey push", "config": _config(), **kw}, headers=_h(token))


async def test_create_get_and_find_contacts_round_trip(client) -> None:
    token, _, pid = await _setup(client, "o1@example.com")
    eligible = await client.get(f"{BASE}/eligible-clinics?countries=TR,TH", headers=_h(token))
    assert eligible.status_code == 200 and [c["id"] for c in eligible.json()["clinics"]] == [str(pid)]

    created = await _create(client, token)
    assert created.status_code == 201, created.text
    cid = created.json()["campaign_id"]

    found = await client.post(f"{BASE}/campaigns/{cid}/find-contacts", headers=_h(token))
    assert found.status_code == 200 and found.json()["found"] == 1 and found.json()["search_configured"] is True

    got = (await client.get(f"{BASE}/campaigns/{cid}", headers=_h(token))).json()
    assert got["campaign"]["id"] == cid and got["mode"] == "SHADOW"
    assert got["config"]["term_sheet"]["currency"] == "USD"
    assert got["clinics"][0]["contact_email"] == "international@alpha.example"


@pytest.mark.parametrize("bad", ["150", "100.01", "-0.01", "0"])
async def test_invalid_term_sheet_is_422(client, bad) -> None:
    token, _, _ = await _setup(client, "o2@example.com")
    r = await client.post(f"{BASE}/campaigns", json={"name": "x", "config": _config(max_value=bad)}, headers=_h(token))
    assert r.status_code == 422


async def test_percentage_boundaries_100_is_accepted_and_non_finite_is_422(client) -> None:
    token, _, _ = await _setup(client, "o2b@example.com")
    assert (await _create(client, token, config=_config(min_value="0.01", max_value="100"))).status_code == 201
    for raw in ("NaN", "Infinity"):
        body = '{"name":"x","config":' + __import__("json").dumps(_config(max_value="1")).replace('"max_value": "1"', f'"max_value": "{raw}"') + "}"
        r = await client.post(f"{BASE}/campaigns", content=body, headers={**_h(token), "Content-Type": "application/json"})
        assert r.status_code == 422, raw


async def test_a_user_without_manage_campaigns_cannot_create_or_start(client) -> None:
    owner, tid, _ = await _setup(client, "o3@example.com")
    cid = (await _create(client, owner)).json()["campaign_id"]
    ro = await _readonly_token(client, tid, "ro3@example.com")
    assert (await client.get(f"{BASE}/campaigns/{cid}", headers=_h(ro))).status_code == 200  # READ_MARKETING suffices to read
    assert (await client.get(f"{BASE}/suppressions", headers=_h(ro))).status_code == 200
    calls = [
        ("post", f"{BASE}/campaigns", {"name": "x", "config": _config()}),
        ("post", f"{BASE}/campaigns/{cid}/start", None),
        ("post", f"{BASE}/campaigns/{cid}/pause", None),
        ("post", f"{BASE}/campaigns/{cid}/stop", None),
        ("post", f"{BASE}/campaigns/{cid}/find-contacts", None),
        ("put", f"{BASE}/campaigns/{cid}/contacts/{uuid.uuid4()}", {"email": "a@clinic.example"}),
        ("post", f"{BASE}/suppressions", {"value": "x.example", "kind": "domain"}),
    ]
    for method, url, body in calls:
        r = await getattr(client, method)(url, headers=_h(ro), **({"json": body} if body is not None else {}))
        assert r.status_code == 403, (url, r.status_code)


async def test_another_tenant_gets_404_for_the_campaign(client) -> None:
    owner, _, pid = await _setup(client, "o4@example.com")
    cid = (await _create(client, owner)).json()["campaign_id"]
    other = await _register(client, "Other Org", "o4b@example.com")
    h = _h(other)
    assert (await client.get(f"{BASE}/campaigns/{cid}", headers=h)).status_code == 404
    assert (await client.post(f"{BASE}/campaigns/{cid}/start", headers=h)).status_code == 404
    assert (await client.post(f"{BASE}/campaigns/{cid}/find-contacts", headers=h)).status_code == 404
    assert (await client.put(f"{BASE}/campaigns/{cid}/contacts/{pid}", json={"email": "a@clinic.example"}, headers=h)).status_code == 404
    assert (await client.get(f"{BASE}/campaigns/{uuid.uuid4()}", headers=_h(owner))).status_code == 404


async def test_start_pause_stop_change_the_campaign_status(client) -> None:
    token, _, _ = await _setup(client, "o5@example.com")
    cid = (await _create(client, token)).json()["campaign_id"]

    async def status():
        return (await client.get(f"{BASE}/campaigns/{cid}", headers=_h(token))).json()["campaign"]["status"]

    assert (await client.post(f"{BASE}/campaigns/{cid}/pause", headers=_h(token))).status_code == 409  # not active yet
    assert (await client.post(f"{BASE}/campaigns/{cid}/start", headers=_h(token))).status_code == 200
    assert await status() == "ACTIVE"
    assert (await client.post(f"{BASE}/campaigns/{cid}/pause", headers=_h(token))).status_code == 200
    assert await status() == "PAUSED"
    assert (await client.post(f"{BASE}/campaigns/{cid}/stop", headers=_h(token))).status_code == 200
    assert await status() == "COMPLETED"
    assert (await client.post(f"{BASE}/campaigns/{cid}/start", headers=_h(token))).status_code == 409


async def test_set_contact_with_a_free_mail_address_is_409(client) -> None:
    token, _, pid = await _setup(client, "o6@example.com")
    cid = (await _create(client, token)).json()["campaign_id"]
    bad = await client.put(f"{BASE}/campaigns/{cid}/contacts/{pid}", json={"email": "someone@gmail.com"}, headers=_h(token))
    assert bad.status_code == 409 and "business address" in bad.json()["detail"]
    ok = await client.put(f"{BASE}/campaigns/{cid}/contacts/{pid}", json={"email": "info@alpha.example"}, headers=_h(token))
    assert ok.status_code == 200
    got = (await client.get(f"{BASE}/campaigns/{cid}", headers=_h(token))).json()
    assert got["clinics"][0]["contact_email"] == "info@alpha.example" and got["clinics"][0]["stage"] == "READY"


async def test_suppressions_can_be_added_listed_and_block_contacts(client) -> None:
    token, _, pid = await _setup(client, "o7@example.com")
    cid = (await _create(client, token)).json()["campaign_id"]
    assert (await client.post(f"{BASE}/suppressions", json={"value": "alpha.example", "kind": "domain", "reason": "asked"}, headers=_h(token))).status_code == 200
    assert (await client.post(f"{BASE}/suppressions", json={"value": "not an address", "kind": "address"}, headers=_h(token))).status_code == 409
    listed = (await client.get(f"{BASE}/suppressions", headers=_h(token))).json()["suppressions"]
    assert [(s["kind"], s["value"]) for s in listed] == [("domain", "alpha.example")]
    blocked = await client.put(f"{BASE}/campaigns/{cid}/contacts/{pid}", json={"email": "info@alpha.example"}, headers=_h(token))
    assert blocked.status_code == 409
    other = await _register(client, "Other Org 7", "o7b@example.com")
    assert (await client.get(f"{BASE}/suppressions", headers=_h(other))).json()["suppressions"] == []


async def test_unauthenticated_requests_are_401(client) -> None:
    cid, pid = uuid.uuid4(), uuid.uuid4()
    for method, url, body in [
        ("get", f"{BASE}/eligible-clinics?countries=TR", None),
        ("post", f"{BASE}/campaigns", {"name": "x", "config": _config()}),
        ("get", f"{BASE}/campaigns/{cid}", None),
        ("post", f"{BASE}/campaigns/{cid}/find-contacts", None),
        ("put", f"{BASE}/campaigns/{cid}/contacts/{pid}", {"email": "a@b.example"}),
        ("post", f"{BASE}/campaigns/{cid}/start", None),
        ("post", f"{BASE}/campaigns/{cid}/pause", None),
        ("post", f"{BASE}/campaigns/{cid}/stop", None),
        ("get", f"{BASE}/suppressions", None),
        ("post", f"{BASE}/suppressions", {"value": "x.example", "kind": "domain"}),
    ]:
        r = await getattr(client, method)(url, **({"json": body} if body is not None else {}))
        assert r.status_code == 401, (method, url)
