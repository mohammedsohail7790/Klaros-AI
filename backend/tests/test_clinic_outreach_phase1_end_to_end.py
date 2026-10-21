"""Clinic outreach Phase 1 end to end through the real app: fake search, shadow start, audit trail, nothing sent, tenant isolation."""
from __future__ import annotations

import importlib

import pytest
from sqlalchemy import select

from app.api.v1.outreach import get_outreach_service
from app.db.session import async_session_maker
from app.main import app
from app.models.audit_log import AuditLog
from app.services.clinic_outreach_service import ClinicOutreachService
from app.services.mt_clinic_sourcing import SearchHit
from tests.test_business_journey_api import _register
from tests.test_halla_integration import _h, _tenant_id
from tests.test_outreach_api import BASE, _clinic, _config

# Every email/channel send path in the codebase; each must exist (so the zero-call assertion is not vacuous) and must never run in Phase 1.
SEND_PATHS = [
    ("app.communications.brevo_adapter", "BrevoEmailAdapter.send_email"),
    ("app.communications.composite_adapter", "CompositeCommunicationAdapter.send_email"),
    ("app.communications.composite_adapter", "CompositeCommunicationAdapter.send_sms"),
    ("app.communications.consent_guard", "ConsentGuardedProvider.send_email"),
    ("app.communications.smtp_adapter", "SmtpEmailAdapter.send_email"),
    ("app.communications.sendgrid_adapter", "SendGridEmailAdapter.send_email"),
    ("app.communications.twilio_adapter", "TwilioSMSAdapter.send_email"),
    ("app.communications.whatsapp_adapter", "WhatsAppAdapter.send_email"),
    ("app.communications.internal_test_adapter", "InternalTestCommunicationAdapter.send_email"),
    ("app.communications.smtp_client", "_send_blocking"),
]


class OneFindableSearch:
    name = "fake"

    async def search(self, query, max_results=5):
        if query.startswith("Findable Clinic "):
            return [SearchHit("Findable Clinic - Contact", "https://www.findable.example/contact", "Write to international@findable.example for a quote.")]
        return [SearchHit("Form Only - Contact", "https://www.formonly.example/contact", "Use the contact form on this page to reach us.")]


async def _mx_ok(domain: str) -> bool:
    return True


def _resolve(module: str, dotted: str):
    mod = importlib.import_module(module)
    owner, _, attr = dotted.rpartition(".")
    target = getattr(mod, owner) if owner else mod
    assert hasattr(target, attr), f"{module}.{dotted} does not exist; the no-send assertion would be vacuous"
    return target, attr


async def test_phase_1_flow_shadow_start_audits_sends_nothing_and_is_tenant_isolated(client, monkeypatch) -> None:
    calls: list[str] = []
    for module, dotted in SEND_PATHS:
        target, attr = _resolve(module, dotted)
        def _boom(*a, _n=f"{module}.{dotted}", **k):
            calls.append(_n)
            raise AssertionError(f"send path called in Phase 1: {_n}")
        monkeypatch.setattr(target, attr, _boom)

    svc = ClinicOutreachService(async_session_maker, search_factory=lambda: OneFindableSearch(), mx_exists=_mx_ok)
    app.dependency_overrides[get_outreach_service] = lambda: svc
    try:
        token = await _register(client, "Org Phase1", "phase1@example.com")
        tid = await _tenant_id(client, token)
        findable = await _clinic(tid, "Findable Clinic")
        form_only = await _clinic(tid, "Form Only")
        h = _h(token)

        created = await client.post(f"{BASE}/campaigns", json={"name": "Turkey push", "config": _config()}, headers=h)
        assert created.status_code == 201, created.text
        cid = created.json()["campaign_id"]

        found = await client.post(f"{BASE}/campaigns/{cid}/find-contacts", headers=h)
        assert found.status_code == 200 and found.json()["found"] == 1 and found.json()["no_email"] == 1

        got = (await client.get(f"{BASE}/campaigns/{cid}", headers=h)).json()
        funnel = {k: v for k, v in got["funnel"].items() if v}
        assert funnel == {"READY": 1, "NO_EMAIL": 1}
        by_provider = {c["provider_id"] if "provider_id" in c else c["id"]: c for c in got["clinics"]}
        assert by_provider[str(findable)]["stage"] == "READY" and by_provider[str(form_only)]["stage"] == "NO_EMAIL"

        bad = await client.put(f"{BASE}/campaigns/{cid}/contacts/{form_only}", json={"email": "someone@gmail.com"}, headers=h)
        assert bad.status_code == 409
        ok = await client.put(f"{BASE}/campaigns/{cid}/contacts/{form_only}", json={"email": "info@formonly.example"}, headers=h)
        assert ok.status_code == 200
        got = (await client.get(f"{BASE}/campaigns/{cid}", headers=h)).json()
        assert {k: v for k, v in got["funnel"].items() if v} == {"READY": 2}

        assert (await client.post(f"{BASE}/campaigns/{cid}/start", headers=h)).status_code == 200
        got = (await client.get(f"{BASE}/campaigns/{cid}", headers=h)).json()
        assert got["campaign"]["status"] == "ACTIVE" and got["mode"] == "SHADOW"

        async with async_session_maker() as s:
            rows = list((await s.execute(select(AuditLog).where(AuditLog.tenant_id == tid, AuditLog.action.like("outreach.%")))).scalars())
        actions = sorted(r.action for r in rows)
        assert actions == ["outreach.campaign_created", "outreach.campaign_started", "outreach.contact_found", "outreach.contact_set"]
        assert all("@" not in str(r.input_summary) for r in rows)

        assert calls == []

        other = await _register(client, "Other Org Phase1", "phase1b@example.com")
        oh = _h(other)
        assert (await client.get(f"{BASE}/campaigns/{cid}", headers=oh)).status_code == 404
        assert (await client.get(f"{BASE}/suppressions", headers=oh)).json()["suppressions"] == []
        eligible = (await client.get(f"{BASE}/eligible-clinics?countries=TR", headers=oh)).json()["clinics"]
        assert not {str(findable), str(form_only)} & {c["id"] for c in eligible}
    finally:
        app.dependency_overrides.pop(get_outreach_service, None)
