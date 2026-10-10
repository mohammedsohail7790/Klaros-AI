"""Halla -> Klaros contract: the cases the first fixtures did not pin down. Consent-only `lead.updated` ({leadId, consent} and nothing else, as Halla's
outbox publishes it), `lead.escalated` with human targets and safe reason codes, a `lead.qualified` that lacks its required `qualification`, duplicate
and out-of-order consent. Bodies are signed with the same algorithm Halla's signer uses (proved identical by tests/test_halla_contract_mt.py).
In-process HTTP, not a live delivery."""
from __future__ import annotations

import json
import time
import uuid

import pytest

from tests.test_halla_contract_mt import HTENANT, SECRET, _consent_raw, _evidence, _mt, _state, _events_of
from tests.test_halla_integration import _deliver, _lead, _lead_state, halla  # noqa: F401

HALLA_LEAD = "halla-lead-9001"


def _consent_only(scope, *, granted, at, wording="owner-label-v1", event_id=None) -> bytes:
    """Exactly what Halla's durable outbox publishes: data = { leadId, consent } — no name, no phone, no free text."""
    return json.dumps({
        "id": event_id or f"evt-co-{time.time_ns()}", "type": "lead.updated", "timestamp": at, "tenant_id": HTENANT,
        "data": {"leadId": HALLA_LEAD, "consent": {"granted": granted, "scope": scope, "method": "voice_ai_verbal", "wording_version": wording, "recorded_at": at}},
    }).encode()


async def _state_of(tid):
    st = await _state(tid)
    return (st.allows("contact"), st.allows("store_personal_data"), st.allows("store_medical_information"))


async def test_consent_only_updates_apply_withdrawal_regrant_duplicates_and_out_of_order(client, halla) -> None:  # noqa: F811
    token, tid = await _mt(client, "Gap Consent", "gapconsent@example.com")
    await _deliver(client, tid, _consent_raw("granted_all"), secret=SECRET)                       # creates the lead; all three scopes @ 2026-10-09T12:00Z
    lead = [x for x in (await client.get("/api/v1/leads", headers={"Authorization": f"Bearer {token}"})).json()["leads"] if x["name"] == "Synthetic Patient"][0]["id"]
    assert await _state_of(tid) == (True, True, True)

    # Halla's evidence is a COMPLETE-STATE snapshot: a partial withdrawal arrives as `granted: true` listing only the scopes still agreed to
    # (a scope that is not listed is not granted); `granted: false` appears only when nothing is currently granted.
    PM = ["store_personal_data", "store_medical_information"]
    withdraw_contact = _consent_only(PM, granted=True, at="2026-10-09T13:00:00.000Z")
    body = json.loads(withdraw_contact)
    assert set(body["data"]) == {"leadId", "consent"}                                              # the exact shape: nothing else is present
    assert (await _deliver(client, tid, withdraw_contact, secret=SECRET)).json() == {"status": "ok"}
    assert await _state_of(tid) == (False, True, True)                                             # partial withdrawal: only contact changes
    n = len(await _evidence(tid))

    same_decision_again = _consent_only(PM, granted=True, at="2026-10-09T13:00:00.000Z")           # re-published under a NEW event id
    assert (await _deliver(client, tid, same_decision_again, secret=SECRET)).json() == {"status": "ok"}
    assert len(await _evidence(tid)) == n                                                          # a duplicate consent event is a no-op

    assert (await _deliver(client, tid, _consent_only(["store_medical_information"], granted=True, at="2026-10-09T14:00:00.000Z"), secret=SECRET)).status_code == 200
    assert await _state_of(tid) == (False, False, True)
    # the lead is frozen: a qualification arriving now changes nothing
    q = json.dumps({"id": f"evt-q-{time.time_ns()}", "type": "lead.qualified", "timestamp": "2026-10-09T14:05:00.000Z", "tenant_id": HTENANT,
                    "data": {"leadId": HALLA_LEAD, "klarosLeadId": lead, "qualification": "qualified", "status": "qualified"}}).encode()
    await _deliver(client, tid, q, secret=SECRET)
    assert (await _lead_state(client, token, lead))[1] == "PENDING"

    # an OLDER decision delivered late (out of order) cannot undo the withdrawal
    assert (await _deliver(client, tid, _consent_only(["contact", *PM], granted=True, at="2026-10-09T13:30:00.000Z"), secret=SECRET)).status_code == 200
    assert await _state_of(tid) == (False, False, True)

    # a newer re-grant restores exactly what it grants, and the lead is live again
    assert (await _deliver(client, tid, _consent_only(PM, granted=True, at="2026-10-09T15:00:00.000Z"), secret=SECRET)).status_code == 200
    assert await _state_of(tid) == (False, True, True)
    await _deliver(client, tid, json.dumps({**json.loads(q), "id": f"evt-q2-{time.time_ns()}"}).encode(), secret=SECRET)
    assert (await _lead_state(client, token, lead))[1] == "QUALIFIED"

    # full withdrawal (nothing granted): `granted: false` with the scopes that were declined / withdrawn
    assert (await _deliver(client, tid, _consent_only(["contact", *PM], granted=False, at="2026-10-09T16:00:00.000Z"), secret=SECRET)).status_code == 200
    assert await _state_of(tid) == (False, False, False)
    rows = await _evidence(tid)
    assert all("Synthetic" not in str(vars(r)) and "+1555" not in str(vars(r)) for r in rows)


@pytest.mark.parametrize("target", ["human_callback", "human_review"])
@pytest.mark.parametrize("reason", ["emergency_EMERGENCY", "unsafe_statement_DIAGNOSIS_REQUEST", "unsafe_statement_PRESCRIPTION_REQUEST", "unsafe_statement_OUTCOME_GUARANTEE_REQUEST"])
async def test_a_human_escalation_with_a_safe_reason_code_reaches_a_person_and_stores_no_text(client, halla, target, reason) -> None:  # noqa: F811
    token, tid = await _mt(client, f"Gap Esc {target[:7]}", f"gapesc{target[6:9]}{reason[:6].lower()}{reason[-4:].lower()}@example.com")
    lead = await _lead(client, token)
    body = lambda eid: json.dumps({"id": eid, "type": "lead.escalated", "timestamp": "2026-10-09T10:00:00.000Z", "tenant_id": HTENANT,
                                   "data": {"callId": "CA-gap-1", "leadId": HALLA_LEAD, "klarosLeadId": lead, "target": target, "reason": reason}}).encode()
    assert (await _deliver(client, tid, body("evt-esc-1"), secret=SECRET)).json() == {"status": "ok"}
    assert await _lead_state(client, token, lead) == ("NEW", "REQUIRES_HUMAN")                     # a person decides; LeadStatus is untouched
    # Halla reports the same escalation twice (call.completed + lead.escalated, or a retry under a new id): one escalation
    await _deliver(client, tid, body("evt-esc-2"), secret=SECRET)
    esc = await _events_of(tid, "halla.lead.escalated")
    assert len(esc) == 1
    stored = str(esc[0].payload)
    assert "unsafe_statement" not in stored and "human_callback" not in stored and "human_review" not in stored      # codes / targets are routing input, not stored text


async def test_a_lead_qualified_without_its_qualification_changes_nothing_and_fires_no_workflow(client, halla) -> None:  # noqa: F811
    token, tid = await _mt(client, "Gap Qual", "gapqual@example.com")
    lead = await _lead(client, token)
    for i, data in enumerate(({"leadId": HALLA_LEAD, "klarosLeadId": lead}, {"leadId": HALLA_LEAD, "klarosLeadId": lead, "qualification": "unknown"},
                              {"leadId": HALLA_LEAD, "klarosLeadId": lead, "qualification": "definitely-qualified"})):
        raw = json.dumps({"id": f"evt-nq-{i}-{uuid.uuid4().hex[:6]}", "type": "lead.qualified", "timestamp": "2026-10-09T10:00:00.000Z", "tenant_id": HTENANT, "data": data}).encode()
        assert (await _deliver(client, tid, raw, secret=SECRET)).status_code == 200
        assert await _lead_state(client, token, lead) == ("NEW", "PENDING")                          # never defaulted to qualified
    assert await _events_of(tid, "halla.lead.qualified") == []
    ok = json.dumps({"id": f"evt-ok-{uuid.uuid4().hex[:6]}", "type": "lead.qualified", "timestamp": "2026-10-09T10:00:00.000Z", "tenant_id": HTENANT,
                     "data": {"leadId": HALLA_LEAD, "klarosLeadId": lead, "qualification": "qualified", "status": "qualified"}}).encode()
    await _deliver(client, tid, ok, secret=SECRET)
    assert await _lead_state(client, token, lead) == ("QUALIFIED", "QUALIFIED") and len(await _events_of(tid, "halla.lead.qualified")) == 1


async def test_the_connect_script_sets_the_medical_tourism_profile_from_a_non_secret_env_var(client, halla) -> None:  # noqa: F811
    """How the dedicated staging tenant becomes Medical Tourism without editing its start command: HALLA_SAFETY_PROFILE=medical_tourism."""
    from scripts.connect_halla_tenant import connect
    from tests.test_business_journey_api import _register
    from tests.test_halla_integration import API_KEY, HALLA_TENANT, _tenant_id
    from app.services import consent_gate
    from app.db.session import async_session_maker

    token = await _register(client, "Script Profile", "scriptprofile@example.com")
    tid = await _tenant_id(client, token)
    env = {"HALLA_API_KEY": API_KEY, "HALLA_WEBHOOK_SECRET": SECRET, "HALLA_SAFETY_PROFILE": "medical_tourism"}
    assert await consent_gate.tenant_requires_consent(async_session_maker, tid) is False
    result = await connect(tid, HALLA_TENANT, env)
    assert result["status"] == "CONNECTED" and SECRET not in repr(result) and API_KEY not in repr(result)
    assert await consent_gate.tenant_requires_consent(async_session_maker, tid) is True            # the profile turns the consent gate on
    with pytest.raises(SystemExit):
        await connect(tid, HALLA_TENANT, {**env, "HALLA_SAFETY_PROFILE": "not_a_profile"})          # an unknown profile is refused, never ignored
