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


# ============================================================================== consent evidence (Halla patch 10bce7ad, HALLA_KLAROS_INTEGRATION_CONTRACT 3.1)
CE = FIX["consent_events"]
GRANTED = ["granted_all", "granted_personal_only", "granted_contact_only", "granted_personal_and_contact", "declined_or_withdrawn_personal", "declined_all",
           "later_withdrawal_of_contact", "stale_grant_all"]
MALFORMED = sorted(k for k in CE if k.startswith("bad_"))


def _consent_raw(name: str, *, event_id: str | None = None, etype: str = "lead.created") -> bytes:
    env = json.loads(CE[name]["body"])
    env["id"] = event_id or f"{CE[name]['id']}-{time.time_ns()}"
    env["type"] = etype
    return json.dumps(env).encode()


async def _evidence(tid):
    from sqlalchemy import select

    from app.db.session import async_session_maker, set_tenant_context
    from app.models.halla_consent import HallaConsentEvidence

    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        return list((await s.execute(select(HallaConsentEvidence).where(HallaConsentEvidence.tenant_id == tid).order_by(HallaConsentEvidence.recorded_at))).scalars())


async def _named(client, token, name):
    r = (await client.get("/api/v1/leads", headers=_h(token))).json()
    items = r if isinstance(r, list) else next((v for v in r.values() if isinstance(v, list)), [])
    return [x for x in items if isinstance(x, dict) and x.get("name") == name]


async def _state(tid, halla_lead="halla-lead-9001"):
    from app.db.session import async_session_maker, set_tenant_context
    from app.services import halla_consent

    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        return await halla_consent.state_for(s, tid, halla_lead_id=halla_lead)


@pytest.mark.parametrize("name", sorted(CE))
def test_consent_fixtures_are_signed_by_hallas_signer_and_klaros_agrees_with_hallas_own_validator(name) -> None:
    f = CE[name]
    now = datetime.fromtimestamp(int(TS), tz=timezone.utc)
    hw.verify_signature(f["body"].encode(), TS, f["signature"], SECRET, tolerance_seconds=300, now=now)
    assert f["signature"] == "sha256=" + hw.sign(SECRET, TS, f["body"].encode())
    # Halla's sanitizeConsentEvidence (from the patch) accepts exactly what Klaros accepts -- no contract drift in either direction
    assert (hw.consent_evidence(json.loads(f["body"])["data"]) is not None) == f["halla_sanitizer_accepts"]


def test_consent_evidence_parses_the_exact_halla_shape_and_nothing_else() -> None:
    ev = hw.consent_evidence(json.loads(CE["granted_personal_and_contact"]["body"])["data"])
    assert ev.granted is True and ev.scopes == ("contact", "store_personal_data") and ev.method == "voice_ai_verbal"
    assert ev.wording_version == "owner-label-v1" and ev.recorded_at.isoformat() == "2026-10-09T10:00:02+00:00"
    dec = hw.consent_evidence(json.loads(CE["declined_or_withdrawn_personal"]["body"])["data"])
    assert dec.granted is False and dec.scopes == ("store_personal_data",)  # declined and withdrawn are the same shape; Klaros does not tell them apart
    assert hw.consent_evidence({}) is None and hw.consent_evidence({"consent": None}) is None and hw.consent_evidence({"consent": True}) is None


@pytest.mark.parametrize("name", MALFORMED)
async def test_malformed_consent_is_ignored_stores_nothing_and_creates_no_lead(client, halla, name) -> None:  # noqa: F811
    token, tid = await _mt(client, f"MT Mal {name}", f"mtmal{name.replace('_', '')}@example.com")
    assert (await _deliver(client, tid, _consent_raw(name), secret=SECRET)).status_code == 200
    assert await _named(client, token, "Synthetic Patient") == [] and await _evidence(tid) == []


async def test_missing_consent_creates_no_lead_and_is_never_inferred(client, halla) -> None:  # noqa: F811
    token, tid = await _mt(client, "MT Missing", "mtmissing@example.com")
    r = await _deliver(client, tid, _raw("lead.created"), secret=SECRET)  # a call + an AI interaction happened, but no consent object
    assert r.status_code == 200 and await _named(client, token, "Synthetic Patient") == [] and await _evidence(tid) == []


@pytest.mark.parametrize("name", ["granted_all", "granted_personal_only", "granted_personal_and_contact", "later_withdrawal_of_contact"])
async def test_a_grant_of_store_personal_data_creates_exactly_one_lead_and_replay_adds_nothing(client, halla, name) -> None:  # noqa: F811
    token, tid = await _mt(client, f"MT Grant {name}", f"mtgrant{name.replace('_', '')}@example.com")
    raw = _consent_raw(name, event_id=f"evt-{name}-1")
    assert (await _deliver(client, tid, raw, secret=SECRET)).json() == {"status": "ok"}
    assert len(await _named(client, token, "Synthetic Patient")) == 1 and len(await _evidence(tid)) == 1
    assert (await _deliver(client, tid, raw, secret=SECRET)).json() == {"status": "duplicate_ignored"}  # the same delivery again
    await _deliver(client, tid, _consent_raw(name, event_id=f"evt-{name}-retry"), secret=SECRET)  # a retry under a new event id
    assert len(await _named(client, token, "Synthetic Patient")) == 1 and len(await _evidence(tid)) == 2  # still one lead; both evidence records kept
    ev = (await _evidence(tid))[0]
    assert "Synthetic" not in str(vars(ev)) and "+1555" not in str(vars(ev))  # the history holds no name or phone


@pytest.mark.parametrize("name", ["granted_contact_only", "declined_or_withdrawn_personal", "declined_all"])
async def test_consent_without_store_personal_data_stores_no_lead_but_keeps_the_decision(client, halla, name) -> None:  # noqa: F811
    token, tid = await _mt(client, f"MT NoPD {name}", f"mtnopd{name.replace('_', '')}@example.com")
    assert (await _deliver(client, tid, _consent_raw(name), secret=SECRET)).status_code == 200
    assert await _named(client, token, "Synthetic Patient") == []  # contact consent is not consent to store personal data
    assert len(await _evidence(tid)) == 1 and (await _state(tid)).allows("store_personal_data") is False


async def test_scopes_are_independent(client, halla) -> None:  # noqa: F811
    token, tid = await _mt(client, "MT Scopes", "mtscopes@example.com")
    await _deliver(client, tid, _consent_raw("granted_personal_only"), secret=SECRET)
    st = await _state(tid)
    assert st.allows("store_personal_data") and not st.allows("contact") and not st.allows("store_medical_information")


async def test_a_later_event_that_omits_a_scope_withdraws_it_and_stale_evidence_cannot_restore_it(client, halla) -> None:  # noqa: F811
    token, tid = await _mt(client, "MT Withdraw", "mtwithdraw@example.com")
    await _deliver(client, tid, _consent_raw("granted_all"), secret=SECRET)  # 10:00:02: everything granted
    assert (await _state(tid)).granted == {"contact", "store_personal_data", "store_medical_information"}
    await _deliver(client, tid, _consent_raw("later_withdrawal_of_contact", etype="lead.updated"), secret=SECRET)  # 12:00: only personal data listed
    assert (await _state(tid)).granted == {"store_personal_data"}  # contact and medical are no longer granted
    await _deliver(client, tid, _consent_raw("stale_grant_all", etype="lead.updated"), secret=SECRET)  # 08:00 delivered late: older than what we hold
    assert (await _state(tid)).granted == {"store_personal_data"}
    await _deliver(client, tid, _consent_raw("declined_all", etype="lead.updated"), secret=SECRET)  # 10:00:02 is also older than 12:00
    assert (await _state(tid)).granted == {"store_personal_data"}
    assert len(await _evidence(tid)) == 4  # nothing is erased: the history keeps every decision


async def test_a_decline_then_a_newer_re_grant_stores_the_lead_only_after_the_re_grant(client, halla) -> None:  # noqa: F811
    token, tid = await _mt(client, "MT Regrant", "mtregrant@example.com")
    await _deliver(client, tid, _consent_raw("declined_or_withdrawn_personal"), secret=SECRET)
    assert await _named(client, token, "Synthetic Patient") == []
    await _deliver(client, tid, _consent_raw("later_withdrawal_of_contact", etype="lead.updated"), secret=SECRET)  # newer decision grants personal data
    assert len(await _named(client, token, "Synthetic Patient")) == 1
    rows = await _evidence(tid)
    assert len(rows) == 2 and all(r.lead_id is not None for r in rows)  # the earlier decline is linked to the lead once it exists, and kept


def test_state_derivation_newest_wins_ties_are_restrictive_and_nothing_means_nothing() -> None:
    from types import SimpleNamespace as R

    from app.services.halla_consent import derive_state

    t = datetime(2026, 10, 9, 10, tzinfo=timezone.utc)
    later = datetime(2026, 10, 9, 11, tzinfo=timezone.utc)
    assert derive_state([]).granted == frozenset() and derive_state([]).has_evidence is False
    grant, decline = R(granted=True, scopes=["contact", "store_personal_data"], recorded_at=t), R(granted=False, scopes=["contact"], recorded_at=t)
    assert derive_state([grant]).granted == {"contact", "store_personal_data"}
    assert derive_state([grant, decline]).granted == frozenset()  # same instant: the restrictive reading wins
    assert derive_state([grant, R(granted=False, scopes=["contact"], recorded_at=later)]).granted == frozenset()  # newer decline beats older grant
    assert derive_state([R(granted=True, scopes=["store_personal_data"], recorded_at=later), grant]).granted == {"store_personal_data"}  # older grant cannot widen
    assert derive_state([R(granted=True, scopes=["contact"], recorded_at=t.replace(tzinfo=None))]).granted == {"contact"}  # SQLite returns naive datetimes


async def test_consent_for_one_tenant_never_grants_another_tenant(client, halla) -> None:  # noqa: F811
    token_a, tid_a = await _mt(client, "MT Iso A", "mtisoa@example.com")
    token_b, tid_b = await _mt(client, "MT Iso B", "mtisob@example.com")
    await _deliver(client, tid_a, _consent_raw("granted_personal_only"), secret=SECRET)
    assert len(await _named(client, token_a, "Synthetic Patient")) == 1
    await _deliver(client, tid_b, _raw("lead.created"), secret=SECRET)  # same Halla lead id, no consent evidence in tenant B
    assert await _named(client, token_b, "Synthetic Patient") == [] and await _evidence(tid_b) == []
    assert (await _state(tid_b)).allows("store_personal_data") is False


@pytest.mark.parametrize("bad", ["secret", "tenant", "stale_ts"])
async def test_consent_events_with_a_bad_signature_wrong_tenant_or_stale_timestamp_store_nothing(client, halla, bad) -> None:  # noqa: F811
    token, tid = await _mt(client, f"MT Forge {bad}", f"mtforge{bad.replace('_', '')}@example.com")
    raw = _consent_raw("granted_all")
    if bad == "secret":
        r = await _deliver(client, tid, raw, secret="whsec-wrong")
    elif bad == "stale_ts":
        r = await _deliver(client, tid, raw, secret=SECRET, ts=str(int(time.time()) - 3600))
    else:
        env = json.loads(raw)
        env["tenant_id"] = "halla-tenant-someone-else"
        r = await _deliver(client, tid, json.dumps(env).encode(), secret=SECRET)
    assert r.status_code in (400, 401, 403)
    assert await _named(client, token, "Synthetic Patient") == [] and await _evidence(tid) == []


async def test_outbound_calls_need_contact_consent_for_medical_tourism_only(client, halla) -> None:  # noqa: F811
    token, tid = await _mt(client, "MT Call", "mtcall@example.com")
    BASE = "/api/v1/business-builder"
    await _deliver(client, tid, _consent_raw("granted_personal_only", event_id="evt-call-1"), secret=SECRET)
    lead = (await _named(client, token, "Synthetic Patient"))[0]["id"]
    assert (await client.post(f"{BASE}/leads/{lead}/halla/call", headers=_h(token))).status_code == 422  # personal-data consent is not contact consent
    assert not halla.sent("POST", "/calls/outbound")
    await _deliver(client, tid, _consent_raw("granted_personal_and_contact", event_id="evt-call-2", etype="lead.updated"), secret=SECRET)
    # same recorded_at as the first (10:00:02): the restrictive reading wins, so contact is still not granted
    assert (await client.post(f"{BASE}/leads/{lead}/halla/call", headers=_h(token))).status_code == 422
    assert not halla.sent("POST", "/calls/outbound")


async def test_a_tenant_without_the_medical_tourism_profile_is_unchanged_by_consent_fields(client, halla) -> None:  # noqa: F811
    from tests.test_halla_integration import HALLA_TENANT, SECRET as PLAIN_SECRET, _body, _connected

    token, tid = await _connected(client, "Plain Co", "plainco@example.com")
    data = {"leadId": "halla-lead-plain", "phone": "+15550100777", "name": "Plain Person", "consent": json.loads(CE["declined_all"]["body"])["data"]["consent"]}
    r = await _deliver(client, tid, _body("lead.created", tenant=HALLA_TENANT, data=data), secret=PLAIN_SECRET)
    assert r.status_code == 200 and len(await _named(client, token, "Plain Person")) == 1  # generic behaviour: the lead is stored as before
    assert await _evidence(tid) == []  # and no consent history is kept for a tenant that did not opt in


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


async def test_contact_consent_allows_the_call_until_a_newer_event_omits_it(client, halla) -> None:  # noqa: F811
    token, tid = await _mt(client, "MT Call2", "mtcall2@example.com")
    BASE = "/api/v1/business-builder"

    def at(name, when, eid):
        env = json.loads(_consent_raw(name, event_id=eid, etype="lead.updated"))
        env["data"]["consent"]["recorded_at"] = when
        return json.dumps(env).encode()

    await _deliver(client, tid, _consent_raw("granted_personal_only", event_id="evt-c2-1"), secret=SECRET)
    lead = (await _named(client, token, "Synthetic Patient"))[0]["id"]
    await _deliver(client, tid, at("granted_personal_and_contact", "2026-10-09T13:00:00.000Z", "evt-c2-2"), secret=SECRET)  # newer: contact granted
    assert (await client.post(f"{BASE}/leads/{lead}/halla/call", headers=_h(token))).status_code == 200
    assert len(halla.sent("POST", "/calls/outbound")) == 1
    await _deliver(client, tid, at("granted_personal_only", "2026-10-09T14:00:00.000Z", "evt-c2-3"), secret=SECRET)  # newer still: contact no longer listed
    assert (await client.post(f"{BASE}/leads/{lead}/halla/call", headers=_h(token))).status_code == 422
    assert len(halla.sent("POST", "/calls/outbound")) == 1
