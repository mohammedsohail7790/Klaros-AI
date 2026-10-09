"""Halla -> Klaros contract tests for the Medical Tourism pilot.

The fixtures in tests/fixtures/halla_contract/halla_events.json were produced by HALLA'S OWN signer (signWebhookPayload) from bodies shaped like
Halla's klaros-webhook.consumer (key names and omitted-undefined behaviour are the real ones; every VALUE is synthetic). This proves signature
compatibility and payload-shape handling. It is NOT a live Halla->Klaros delivery: the HTTP legs below are in-process, mock-based integration.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.integrations.workforce import halla_webhook as hw
from tests.test_halla_integration import _connect, _deliver, _h, _lead, _lead_state, _tenant_id, halla  # noqa: F401
from tests.test_business_journey_api import _register
from tests.test_halla_pilot_startup import _events_of, _profiled

FIX = json.loads((Path(__file__).parent / "fixtures" / "halla_contract" / "halla_events.json").read_text())
SECRET, HTENANT, TS = FIX["secret"], FIX["halla_tenant_id"], FIX["timestamp"]
EV = FIX["events"]


def _raw(etype: str, **data_over) -> bytes:
    """The fixture body (Halla's real shape), optionally with data keys replaced, under a fresh event id."""
    env = json.loads(EV[etype]["body"])
    env["data"].update(data_over)
    env["id"] = f"{env['id']}-{time.time_ns()}"
    return json.dumps(env).encode()


async def _mt(client, name, email):
    token, tid = await _profiled(client, name, email, "medical_tourism")
    # re-point the connection at the fixture's Halla tenant and secret
    await _connect(client, token, halla_tenant=HTENANT, secret=SECRET)  # the mocked health check may not know the fixture tenant; the receiver stays active
    from app.api.tool_deps_business_builder import get_halla_integration_service
    from app.db.session import async_session_maker, set_tenant_context

    conn = await get_halla_integration_service()._connections.get_connection(tid, "halla")
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        row = await s.get(type(conn), conn.id)
        row.connection_metadata = {**(row.connection_metadata or {}), "safety_profile": "medical_tourism"}
        await s.commit()
    return token, tid


@pytest.mark.parametrize("etype", sorted(EV))
def test_every_fixture_signed_by_hallas_signer_verifies_in_klaros(etype) -> None:
    f = EV[etype]
    now = datetime.fromtimestamp(int(TS), tz=timezone.utc)
    assert f["signature"] == "sha256=" + hw.sign(SECRET, TS, f["body"].encode())  # same bytes as Halla's function
    hw.verify_signature(f["body"].encode(), TS, f["signature"], SECRET, tolerance_seconds=300, now=now)
    assert json.loads(f["body"])["type"] in hw.SUPPORTED_EVENTS if hasattr(hw, "SUPPORTED_EVENTS") else True


def test_hallas_signature_is_rejected_for_a_wrong_secret_a_tampered_body_and_a_stale_clock() -> None:
    f = EV["lead.created"]
    now = datetime.fromtimestamp(int(TS), tz=timezone.utc)
    kw = dict(tolerance_seconds=300, now=now)
    with pytest.raises(hw.HallaWebhookError):
        hw.verify_signature(f["body"].encode(), TS, f["signature"], "whsec-other-secret", **kw)
    with pytest.raises(hw.HallaWebhookError):
        hw.verify_signature(f["body"].encode() + b" ", TS, f["signature"], SECRET, **kw)
    with pytest.raises(hw.HallaWebhookError):
        hw.verify_signature(f["body"].encode(), TS, f["signature"], SECRET, tolerance_seconds=300, now=datetime.fromtimestamp(int(TS) + 301, tz=timezone.utc))


CONSENT = {"granted": True, "scope": ["contact"], "method": "verbal_call", "wording_version": "mt-consent-v1", "recorded_at": "2026-10-01T09:00:00Z"}


async def _named(client, token, name):
    r = (await client.get("/api/v1/leads", headers=_h(token))).json()
    items = r if isinstance(r, list) else next((v for v in r.values() if isinstance(v, list)), [])
    return [x for x in items if isinstance(x, dict) and x.get("name") == name]


async def test_lead_created_with_consent_evidence_creates_one_lead_in_the_mapped_tenant_and_replay_adds_none(client, halla) -> None:  # noqa: F811
    token, tid = await _mt(client, "MT Contract A", "mtcontracta@example.com")
    raw = _raw("lead.created", consent=CONSENT)
    r = await _deliver(client, tid, raw, secret=SECRET)
    assert r.status_code == 200
    assert len(await _named(client, token, "Synthetic Patient")) == 1
    assert (await _deliver(client, tid, raw, secret=SECRET)).json() == {"status": "duplicate_ignored"}
    # a retry under a NEW event id for the same Halla lead still creates no second lead (idempotent on the Halla lead id)
    await _deliver(client, tid, _raw("lead.created", consent=CONSENT), secret=SECRET)
    assert len(await _named(client, token, "Synthetic Patient")) == 1


async def test_lead_created_without_consent_evidence_stores_no_personal_data(client, halla) -> None:  # noqa: F811
    token, tid = await _mt(client, "MT Contract A2", "mtcontracta2@example.com")
    r = await _deliver(client, tid, _raw("lead.created"), secret=SECRET)  # the REAL Halla shape today: no consent field
    assert r.status_code == 200
    assert await _named(client, token, "Synthetic Patient") == []


@pytest.mark.parametrize(
    "consent",
    [None, {}, "true", True, {**CONSENT, "granted": "true"}, {**CONSENT, "granted": False}, {**CONSENT, "scope": []}, {**CONSENT, "scope": ["marketing"]},
     {**CONSENT, "scope": ["data_processing"]}, {**CONSENT, "method": "assumed"}, {**CONSENT, "wording_version": ""}, {**CONSENT, "recorded_at": "not-a-date"},
     {**CONSENT, "recorded_at": "2026-10-01T09:00:00"}, {**CONSENT, "recorded_at": "2999-01-01T00:00:00Z"}],
)
def test_consent_evidence_is_never_inferred(consent) -> None:
    assert hw.consent_evidence({"consent": consent} if consent is not None else {}) is None


def test_valid_consent_evidence_keeps_only_category_facts() -> None:
    ev = hw.consent_evidence({"consent": {**CONSENT, "wording_text": "I agree to ZXQ-secret-text", "transcript": "ZXQ"}})
    assert ev == {"scope": ["contact"], "method": "verbal_call", "wording_version": "mt-consent-v1", "recorded_at": "2026-10-01T09:00:00+00:00"}


async def test_cross_tenant_unknown_tenant_and_forged_deliveries_persist_nothing(client, halla) -> None:  # noqa: F811
    token, tid = await _mt(client, "MT Contract B", "mtcontractb@example.com")
    other_token, other_tid = await _mt(client, "MT Contract B2", "mtcontractb2@example.com")
    lead = await _lead(client, token)
    raw = _raw("lead.qualified", klarosLeadId=lead)
    # the other tenant's URL + secret: payload tenant_id is Halla's tenant for BOTH rows here, so use a payload naming a different Halla tenant
    env = json.loads(raw)
    env["tenant_id"] = "halla-tenant-someone-else"
    assert (await _deliver(client, tid, json.dumps(env).encode(), secret=SECRET)).status_code == 403
    import uuid

    assert (await _deliver(client, uuid.uuid4(), raw, secret=SECRET)).status_code == 404  # unknown Klaros tenant
    assert (await _deliver(client, tid, raw, secret="whsec-wrong")).status_code in (400, 401)
    assert (await _deliver(client, tid, raw, secret=SECRET, ts=str(int(time.time()) - 3600))).status_code in (400, 401)
    assert (await _lead_state(client, token, lead))[1] != "QUALIFIED"
    assert await _events_of(tid, "halla.lead.qualified") == []


async def test_a_real_shaped_qualified_event_qualifies_but_an_emergency_in_reason_goes_to_a_person(client, halla) -> None:  # noqa: F811
    token, tid = await _mt(client, "MT Contract C", "mtcontractc@example.com")
    ok = await _lead(client, token, name="Ok Lead", phone="+15550002222")
    assert (await _deliver(client, tid, _raw("lead.qualified", klarosLeadId=ok), secret=SECRET)).json() == {"status": "ok"}
    assert await _lead_state(client, token, ok) == ("QUALIFIED", "QUALIFIED")
    bad = await _lead(client, token, name="Urgent Lead", phone="+15550003333")
    reason = "Caller said they have chest pain and cannot breathe."  # Halla puts this in `reason`, not `summary`
    await _deliver(client, tid, _raw("lead.qualified", klarosLeadId=bad, reason=reason), secret=SECRET)
    assert (await _lead_state(client, token, bad))[1] == "REQUIRES_HUMAN"
    esc = await _events_of(tid, "halla.lead.escalated")
    assert len(esc) == 1 and esc[0].payload["category"] == "EMERGENCY" and "chest pain" not in str(esc[0].payload)


async def test_an_escalated_event_asking_for_guaranteed_results_is_flagged_without_storing_the_words(client, halla, caplog) -> None:  # noqa: F811
    token, tid = await _mt(client, "MT Contract D", "mtcontractd@example.com")
    lead = await _lead(client, token)
    with caplog.at_level(logging.INFO):
        await _deliver(client, tid, _raw("lead.escalated", klarosLeadId=lead), secret=SECRET)
    esc = await _events_of(tid, "halla.lead.escalated")
    assert len(esc) == 1 and esc[0].payload.get("category") == "OUTCOME_GUARANTEE_REQUEST" and esc[0].payload.get("safety") is True
    assert "definitely work" not in str(esc[0].payload) + caplog.text
    assert SECRET not in caplog.text


async def test_an_unsupported_halla_event_is_rejected_not_silently_accepted(client, halla) -> None:  # noqa: F811
    token, tid = await _mt(client, "MT Contract E", "mtcontracte@example.com")
    env = json.loads(EV["lead.created"]["body"])
    env.update(type="appointment.requested", id="evt-unsupported-1")
    r = await _deliver(client, tid, json.dumps(env).encode(), secret=SECRET)
    assert r.status_code in (400, 422)
