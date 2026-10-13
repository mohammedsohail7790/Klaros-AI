"""Clinic sourcing: finds clinics from search results, never invents one, never marks one VERIFIED, never contacts anyone.
SQLite + in-process HTTP; the search provider and the AI reader are fakes."""
from __future__ import annotations

import json

from sqlalchemy import select

from app.db.session import async_session_maker, set_tenant_context
from app.models.audit_log import AuditLog
from app.models.medical_tourism import Provider, ProviderCredential
from app.services import mt_clinic_sourcing as cs
from app.services.ai_provider import AICallOutcome
from tests.test_consent_gate_intake import _gated
from tests.test_halla_integration import _h, halla  # noqa: F401

BASE = "/api/v1/medical-tourism"
PAGE = cs.SearchHit(
    "International patients - Turkey",
    "https://example.test/turkey-hospitals",
    "Acme Medical Park is a JCI accredited hospital in Istanbul, Turkey. Contact international@acme-park.example or +90 212 555 0100. "
    "Phantom Clinic is not listed here.",
)


class FakeSearch:
    name = "fake"

    def __init__(self, hits):
        self.hits, self.queries = hits, []

    async def search(self, query, max_results=5):
        self.queries.append(query)
        return self.hits


class FakeAI:
    is_connected = True
    name = "fake"
    model = "fake"

    def __init__(self, clinics):
        self.clinics = clinics

    async def generate_structured(self, prompt):
        return AICallOutcome(success=True, provider="fake", model="fake", latency_ms=1, raw_text="sure " + json.dumps({"clinics": self.clinics}))


MODEL_OUTPUT = [
    {"name": "Acme Medical Park", "city": "Istanbul", "email": "international@acme-park.example", "phone": "+90 212 555 0100"},
    {"name": "Invented Hospital", "city": "Ankara", "email": "ghost@invented.example", "phone": "+90 312 000 0000"},  # not in the page text
]


def test_only_what_the_page_text_supports_is_kept() -> None:
    kept, rejected = cs.validate_candidates({"clinics": MODEL_OUTPUT}, PAGE, "TR")
    assert [c.name for c in kept] == ["Acme Medical Park"] and rejected == 1
    c = kept[0]
    assert c.email == "international@acme-park.example" and c.phone and c.city == "Istanbul" and c.accredited


def test_an_email_or_phone_not_on_the_page_is_dropped_but_the_clinic_stays() -> None:
    out = [{"name": "Acme Medical Park", "email": "made-up@acme-park.example", "phone": "+1 555 123 4567", "city": "Paris"}]
    kept, _ = cs.validate_candidates({"clinics": out}, PAGE, "TR")
    assert kept[0].email is None and kept[0].phone is None and kept[0].city is None


def test_a_page_that_never_mentions_the_country_yields_nothing() -> None:
    assert cs.validate_candidates({"clinics": MODEL_OUTPUT}, PAGE, "TH") == ([], 0)
    assert cs.validate_candidates("not a dict", PAGE, "TR") == ([], 0)


async def test_nothing_runs_without_a_search_provider(monkeypatch) -> None:
    monkeypatch.setattr(cs, "search_provider_from_settings", lambda: None)
    import uuid

    r = await cs.source_clinics(uuid.uuid4(), None, ["TR"], ["Dental"])
    assert r.status == "not_configured" and r.added == []


async def test_a_run_adds_unverified_clinics_once_and_never_verifies(client, halla, monkeypatch) -> None:  # noqa: F811
    token, tid = await _gated(client, "Sourcing Co", "sourcing@example.com")
    monkeypatch.setattr(cs, "bound_ai_provider", lambda ai, *a, **k: ai)
    search = FakeSearch([PAGE])
    r = await cs.source_clinics(tid, None, ["TR"], ["Dental"], search=search, ai=FakeAI(MODEL_OUTPUT))
    assert r.status == "ok" and [a["name"] for a in r.added] == ["Acme Medical Park"] and r.rejected_unsupported == 1
    again = await cs.source_clinics(tid, None, ["TR"], ["Dental"], search=search, ai=FakeAI(MODEL_OUTPUT))
    assert again.added == [] and again.skipped_duplicate == 1  # a second run adds nothing

    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        p = (await s.execute(select(Provider).where(Provider.tenant_id == tid))).scalar_one()
        creds = list((await s.execute(select(ProviderCredential).where(ProviderCredential.provider_id == p.id))).scalars())
        audits = list((await s.execute(select(AuditLog).where(AuditLog.tenant_id == tid, AuditLog.action == "mt.provider.auto_sourced"))).scalars())
    assert p.verification_status == "UNDER_REVIEW" and p.country == "TR" and "example.test/turkey-hospitals" in p.description
    assert [c.status for c in creds] == ["PENDING"] and len(audits) == 1

    # automation cannot make it referable: a person still has to verify it, and the backend refuses without a verified credential
    v = await client.put(f"{BASE}/providers/{p.id}/verification", json={"status": "VERIFIED"}, headers=_h(token))
    assert v.status_code == 409


async def test_the_route_needs_countries_and_manage_permission(client, halla, monkeypatch) -> None:  # noqa: F811
    token, _ = await _gated(client, "Route Co", "route@example.com")
    assert (await client.post(f"{BASE}/clinic-sourcing/run", json={}, headers=_h(token))).status_code == 422
    monkeypatch.setattr(cs, "search_provider_from_settings", lambda: None)
    ok = await client.post(f"{BASE}/clinic-sourcing/run", json={"countries": ["TR"]}, headers=_h(token))
    assert ok.status_code == 200 and ok.json()["status"] == "not_configured"
    assert (await client.post(f"{BASE}/clinic-sourcing/run", json={"countries": ["TR"]})).status_code == 401
