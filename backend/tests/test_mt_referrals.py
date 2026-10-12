"""Medical Tourism provider referrals: the backend gate that stops a patient's information reaching a clinic unless the patient approved THAT clinic, plus the
journey from approval to the clinic's response, outcome and commission. SQLite + in-process HTTP (not a deployed service); the only transport is the sandbox.

The rule under test (services/mt_referrals.py): data may be transferred only while the provider is active and currently VERIFIED, the lead's newest consent evidence
grants store_personal_data AND share_with_selected_provider, the newest approval for THIS provider is GRANTED (recorded by a person who told the patient), and every
field is on the approved list. It is checked again on every send, so queued work and retries cannot outlive a withdrawal.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.db.session import async_session_maker, set_tenant_context
from app.models.halla_consent import HallaConsentEvidence
from app.models.medical_tourism import Provider, ProviderCredential
from app.models.medical_tourism_referral import ReferralDelivery
from app.models.rbac import Permission, Role, role_has_permission
from app.services import halla_consent, mt_referrals
from tests.test_consent_gate_intake import ALL, _body, _gated
from tests.test_halla_integration import _h, halla  # noqa: F401

BASE = "/api/v1/medical-tourism"
SHARE = "share_with_selected_provider"


async def _provider(client, token, name="Apex Hospital", country="IN", *, verified=True, expiry: str | None = None):
    p = (await client.post(f"{BASE}/providers", json={"name": name, "country": country, "idempotency_key": f"v-{name}-{uuid.uuid4().hex[:6]}"}, headers=_h(token))).json()["provider"]
    if verified:
        c = (await client.post(f"{BASE}/providers/{p['id']}/credentials", json={"credential_type": "Licence", "expiry_date": expiry}, headers=_h(token))).json()["credential"]
        await client.post(f"{BASE}/providers/{p['id']}/credentials/{c['id']}/verify", headers=_h(token))
        r = await client.put(f"{BASE}/providers/{p['id']}/verification", json={"status": "VERIFIED"}, headers=_h(token))
        assert r.status_code == 200, r.text
    return p


async def _patient(client, token, scopes=ALL, **over):
    n = uuid.uuid4().hex[:8]
    body = _body(consent={"scopes": scopes, "wording_version": "MT-v1"}, name=f"Synthetic Patient {n}", email=f"{n}@example.com", phone=f"+1555{int(n, 16) % 10_000_000:07d}", **over)
    r = await client.post("/api/v1/leads", json=body, headers=_h(token))
    assert r.status_code == 201, r.text
    return r.json()["lead"]["id"]


async def _referral(client, token, lead_id, provider_id):
    r = await client.post(f"{BASE}/referrals", json={"lead_id": lead_id, "provider_id": provider_id}, headers=_h(token))
    assert r.status_code == 200, r.text
    return r.json()["referral"]["id"]


def _grant(fields=("name", "phone", "email"), **over):
    return {"decision": "GRANTED", "permitted_fields": list(fields), "patient_informed": True, "wording_version": "SHARE-v1", "channel": "phone", **over}


async def _approved(client, token, lead_id, provider_id, fields=("name", "phone", "email")):
    ref = await _referral(client, token, lead_id, provider_id)
    r = await client.post(f"{BASE}/referrals/{ref}/approval", json=_grant(fields), headers=_h(token))
    assert r.status_code == 200, r.text
    return ref


async def _send(client, token, ref):
    return await client.post(f"{BASE}/referrals/{ref}/send", headers=_h(token))


async def _deliveries(tid, ref):
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        return list((await s.execute(select(ReferralDelivery).where(ReferralDelivery.tenant_id == tid, ReferralDelivery.referral_id == uuid.UUID(ref)))).scalars())


async def _shared_state(tid, lead_id):
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        return await halla_consent.state_for(s, tid, lead_id=uuid.UUID(lead_id))


# ------------------------------------------------------------------------------ the journey


async def test_the_whole_journey_from_approval_to_paid_commission(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Journey Co", "journey@example.com")
    provider = await _provider(client, token)
    lead = await _patient(client, token)

    ref = await _referral(client, token, lead, provider["id"])
    got = (await client.get(f"{BASE}/referrals/{ref}", headers=_h(token))).json()
    assert got["referral"]["status"] == "DRAFT" and got["gate"] == {"allowed": False, "code": "no_share_consent", "fields": []}  # nothing may go before the patient approves

    r = await client.post(f"{BASE}/referrals/{ref}/approval", json=_grant(), headers=_h(token))
    assert r.status_code == 200 and r.json()["referral"]["status"] == "APPROVED"
    assert (await client.get(f"{BASE}/referrals/{ref}", headers=_h(token))).json()["gate"]["allowed"] is True

    sent = await _send(client, token, ref)
    assert sent.status_code == 200 and sent.json()["newly_sent"] is True and sent.json()["referral"]["status"] == "SENT"
    (d,) = await _deliveries(tid, ref)
    assert d.transport == "sandbox" and sorted(d.fields_sent) == ["email", "name", "phone"] and set(d.payload) == {"reference", "name", "phone", "email"}

    assert (await client.post(f"{BASE}/referrals/{ref}/clinic-response", json={"accepted": True}, headers=_h(token))).json()["referral"]["status"] == "ACCEPTED"

    # a commission is potential until the clinic's reported outcome (with evidence) is on record
    c = (await client.post(f"{BASE}/referrals/{ref}/commission", json={"currency": "usd", "basis": "FLAT", "flat_amount": "500"}, headers=_h(token))).json()["commission"]
    assert c["status"] == "PENDING" and c["currency"] == "USD"
    refused = await client.post(f"{BASE}/commissions/{c['id']}/transition", json={"status": "CONFIRMED"}, headers=_h(token))
    assert refused.status_code == 409 and refused.json()["detail"]["error"] == "no_outcome_evidence"
    assert (await client.post(f"{BASE}/referrals/{ref}/outcome", json={"outcome": "TREATMENT_COMPLETED", "evidence": ""}, headers=_h(token))).status_code == 422  # evidence is required
    out = await client.post(f"{BASE}/referrals/{ref}/outcome", json={"outcome": "TREATMENT_COMPLETED", "evidence": "clinic email 2026-10-30"}, headers=_h(token))
    assert out.json()["referral"]["status"] == "OUTCOME_REPORTED"
    for step in ("CONFIRMED", "INVOICED", "PAID"):
        assert (await client.post(f"{BASE}/commissions/{c['id']}/transition", json={"status": step}, headers=_h(token))).json()["commission"]["status"] == step
    usd = (await client.get(f"{BASE}/revenue", headers=_h(token))).json()["by_currency"]["USD"]
    assert usd["PAID"] == {"count": 1, "amount": "500.00"} and usd["PENDING"]["count"] == 0

    pipe = (await client.get(f"{BASE}/pipeline", headers=_h(token))).json()
    assert [x["stage"] for x in pipe["leads"] if x["lead_id"] == lead] == ["OUTCOME"]


# ------------------------------------------------------------------------------ the fourth consent scope


async def test_nothing_is_sent_without_the_fourth_consent_scope_even_with_the_other_three(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "No Share Co", "noshare@example.com")
    provider = await _provider(client, token)
    lead = await _patient(client, token, ALL)  # contact + store_personal_data + store_medical_information, but NOT share_with_selected_provider
    ref = await _referral(client, token, lead, provider["id"])
    blocked = await _send(client, token, ref)
    assert blocked.status_code == 409 and blocked.json()["detail"]["error"] == "no_share_consent"
    assert await _deliveries(tid, ref) == []
    assert SHARE not in (await _shared_state(tid, lead)).granted


async def test_the_fourth_scope_is_removed_by_a_later_decision_and_the_queued_send_is_blocked(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Queued Co", "queued@example.com")
    provider = await _provider(client, token)
    lead = await _patient(client, token)
    ref = await _approved(client, token, lead, provider["id"])
    assert SHARE in (await _shared_state(tid, lead)).granted
    # the operator later re-records the patient's consent without the sharing scope (a complete snapshot): newest evidence wins
    r = await client.post(f"/api/v1/leads/{lead}/consent", json={"scopes": ["contact", "store_personal_data"]}, headers=_h(token))
    assert r.status_code == 200
    assert SHARE not in (await _shared_state(tid, lead)).granted
    blocked = await _send(client, token, ref)  # the referral is still APPROVED and queued; the gate looks at CURRENT consent
    assert blocked.status_code == 409 and blocked.json()["detail"]["error"] == "no_share_consent"
    assert await _deliveries(tid, ref) == []


async def test_a_withdrawal_reported_by_halla_after_approval_blocks_the_send(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Halla W Co", "hallaw@example.com")
    provider = await _provider(client, token)
    lead = await _patient(client, token)
    ref = await _approved(client, token, lead, provider["id"])
    from app.integrations.workforce.halla_webhook import ConsentEvidence

    async with async_session_maker() as s:  # newer Halla evidence: the patient withdrew personal-data consent on a later call
        await set_tenant_context(s, tid)
        await halla_consent.record(s, tid, event_id=f"halla-w-{uuid.uuid4().hex}", event_type="lead.updated", halla_lead_id=None, lead_id=uuid.UUID(lead),
                                   evidence=ConsentEvidence(False, ("store_personal_data",), "voice_ai_verbal", "owner-label-v1", datetime.now(timezone.utc) + timedelta(seconds=30)))
        await s.commit()
    blocked = await _send(client, token, ref)
    assert blocked.status_code == 409 and blocked.json()["detail"]["error"] == "no_personal_data_consent"
    assert await _deliveries(tid, ref) == []


def test_halla_can_never_grant_sharing_so_a_halla_event_naming_the_fourth_scope_is_not_valid_evidence() -> None:
    from app.integrations.workforce import halla_webhook as hw

    ok = {"granted": True, "scope": ["contact"], "method": "voice_ai_verbal", "wording_version": "v1", "recorded_at": "2026-10-09T10:00:00Z"}
    assert hw.consent_evidence({"consent": ok}) is not None
    assert hw.consent_evidence({"consent": {**ok, "scope": ["contact", SHARE]}}) is None
    assert hw.consent_evidence({"consent": {**ok, "scope": [SHARE]}}) is None
    assert SHARE not in hw.CONSENT_SCOPES


# ------------------------------------------------------------------------------ approval is the patient's decision, recorded by a person


async def test_a_grant_must_be_the_patients_informed_decision_with_listed_fields_and_a_wording_version(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Approve Co", "approve@example.com")
    provider = await _provider(client, token)
    lead = await _patient(client, token)
    ref = await _referral(client, token, lead, provider["id"])

    async def post(**over):
        return await client.post(f"{BASE}/referrals/{ref}/approval", json={**_grant(), **over}, headers=_h(token))

    assert (await post(patient_informed=False)).json()["detail"]["error"] == "patient_must_be_informed"  # a coordinator cannot grant on the patient's behalf
    assert (await post(wording_version="")).json()["detail"]["error"] == "wording_version_required"
    assert (await post(permitted_fields=[])).json()["detail"]["error"] == "fields_required"
    assert (await post(permitted_fields=["name", "diagnosis"])).json()["detail"]["error"] == "fields_required"  # only listed, shareable fields can ever be approved
    assert (await post(channel="carrier_pigeon")).json()["detail"]["error"] == "unknown_channel"
    assert (await post(decision="MAYBE")).status_code == 422
    assert await _deliveries(tid, ref) == [] and SHARE not in (await _shared_state(tid, lead)).granted  # nothing above recorded a grant


async def test_the_treatment_can_be_shared_only_if_the_patient_agreed_to_its_being_kept(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Medical Co", "medical@example.com")
    provider = await _provider(client, token)
    lead = await _patient(client, token, ["contact", "store_personal_data"])  # no store_medical_information
    ref = await _referral(client, token, lead, provider["id"])
    r = await client.post(f"{BASE}/referrals/{ref}/approval", json=_grant(["name", "procedure"]), headers=_h(token))
    assert r.status_code == 409 and r.json()["detail"]["error"] == "no_medical_consent"


async def test_approval_is_provider_specific_and_each_decision_is_kept_in_the_history(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Specific Co", "specific@example.com")
    a = await _provider(client, token, "Clinic A")
    b = await _provider(client, token, "Clinic B", "TR")
    lead = await _patient(client, token)
    ref_a = await _approved(client, token, lead, a["id"])
    ref_b = await _referral(client, token, lead, b["id"])
    blocked = await _send(client, token, ref_b)  # the patient approved clinic A, not clinic B
    assert blocked.status_code == 409 and blocked.json()["detail"]["error"] == "no_provider_approval"
    assert (await _send(client, token, ref_a)).status_code == 200

    ref_b2 = await client.post(f"{BASE}/referrals/{ref_b}/approval", json=_grant(["name"]), headers=_h(token))
    assert ref_b2.status_code == 200
    # withdrawing clinic A does not remove the lead-level scope while clinic B still holds a GRANTED approval; withdrawing B then does
    await client.post(f"{BASE}/referrals/{ref_a}/approval", json={"decision": "WITHDRAWN", "wording_version": "SHARE-v1", "patient_informed": True}, headers=_h(token))
    assert SHARE in (await _shared_state(tid, lead)).granted
    await client.post(f"{BASE}/referrals/{ref_b}/approval", json={"decision": "WITHDRAWN", "wording_version": "SHARE-v1", "patient_informed": True}, headers=_h(token))
    assert SHARE not in (await _shared_state(tid, lead)).granted
    hist = (await client.get(f"{BASE}/referrals/{ref_a}", headers=_h(token))).json()["approvals"]
    assert [h["decision"] for h in hist] == ["WITHDRAWN", "GRANTED"] and all(h["source"] == "coordinator_recorded" for h in hist)  # append-only, newest first


async def test_after_a_withdrawal_the_referral_is_cancelled_and_cannot_be_sent(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Withdraw Co", "withdraw@example.com")
    provider = await _provider(client, token)
    lead = await _patient(client, token)
    ref = await _approved(client, token, lead, provider["id"])
    w = await client.post(f"{BASE}/referrals/{ref}/approval", json={"decision": "WITHDRAWN", "wording_version": "SHARE-v1", "patient_informed": True}, headers=_h(token))
    assert w.json()["referral"]["status"] == "CANCELLED"
    blocked = await _send(client, token, ref)
    assert blocked.status_code == 409 and blocked.json()["detail"]["error"] == "referral_cancelled"
    assert await _deliveries(tid, ref) == []


async def test_a_withdrawal_after_the_data_was_sent_opens_a_task_for_a_person_to_ask_the_clinic_to_remove_it(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Late W Co", "latew@example.com")
    provider = await _provider(client, token)
    lead = await _patient(client, token)
    ref = await _approved(client, token, lead, provider["id"])
    assert (await _send(client, token, ref)).status_code == 200
    await client.post(f"{BASE}/referrals/{ref}/approval", json={"decision": "WITHDRAWN", "wording_version": "SHARE-v1", "patient_informed": True}, headers=_h(token))
    tasks = (await client.get(f"{BASE}/tasks", params={"lead_id": lead}, headers=_h(token))).json()["tasks"]
    assert [(t["kind"], t["reason_code"], t["status"]) for t in tasks] == [("referral_followup", "consent_withdrawn_after_send", "OPEN")]


# ------------------------------------------------------------------------------ only a verified provider


async def test_only_an_active_verified_provider_can_be_referred_to_or_receive_data(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Verify Co", "verify@example.com")
    lead = await _patient(client, token)

    unverified = await _provider(client, token, "Unverified Clinic", verified=False)
    r = await client.post(f"{BASE}/referrals", json={"lead_id": lead, "provider_id": unverified["id"]}, headers=_h(token))
    assert r.status_code == 409 and r.json()["detail"]["error"] == "provider_not_verified"

    # a provider cannot be marked VERIFIED without evidence
    no_cred = await _provider(client, token, "No Evidence Clinic", verified=False)
    r = await client.put(f"{BASE}/providers/{no_cred['id']}/verification", json={"status": "VERIFIED"}, headers=_h(token))
    assert r.status_code == 409 and r.json()["detail"]["error"] == "no_current_verified_credential"
    for status in ("UNDER_REVIEW", "SUSPENDED", "UNVERIFIED"):
        assert (await client.put(f"{BASE}/providers/{no_cred['id']}/verification", json={"status": status}, headers=_h(token))).status_code == 200
    assert (await client.put(f"{BASE}/providers/{no_cred['id']}/verification", json={"status": "TRUSTED"}, headers=_h(token))).status_code == 422

    good = await _provider(client, token, "Good Clinic")
    ref = await _approved(client, token, lead, good["id"])
    # the provider is suspended AFTER the patient approved and before the send: nothing goes
    assert (await client.put(f"{BASE}/providers/{good['id']}/verification", json={"status": "SUSPENDED", "note": "licence query"}, headers=_h(token))).status_code == 200
    blocked = await _send(client, token, ref)
    assert blocked.status_code == 409 and blocked.json()["detail"]["error"] == "provider_not_verified"
    assert await _deliveries(tid, ref) == []


async def test_a_verified_status_whose_credentials_have_all_expired_counts_as_expired(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Expiry Co", "expiry@example.com")
    lead = await _patient(client, token)
    p = await _provider(client, token, "Soon Expired", expiry=(datetime.now(timezone.utc).date() + timedelta(days=30)).isoformat())
    ref = await _approved(client, token, lead, p["id"])
    assert (await client.get(f"{BASE}/providers/{p['id']}/verification", headers=_h(token))).json() == {"provider_id": p["id"], "stored": "VERIFIED", "effective": "VERIFIED", "referable": True}
    async with async_session_maker() as s:  # time passes: the only verified credential is now out of date
        await set_tenant_context(s, tid)
        for c in (await s.execute(select(ProviderCredential).where(ProviderCredential.provider_id == uuid.UUID(p["id"])))).scalars():
            c.expiry_date = datetime.now(timezone.utc).date() - timedelta(days=1)  # the service compares against the UTC date
        await s.commit()
    v = (await client.get(f"{BASE}/providers/{p['id']}/verification", headers=_h(token))).json()
    assert v["stored"] == "VERIFIED" and v["effective"] == "EXPIRED" and v["referable"] is False
    blocked = await _send(client, token, ref)
    assert blocked.status_code == 409 and blocked.json()["detail"]["error"] == "provider_not_verified"


async def test_provider_matching_lists_only_verified_providers(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Match Co", "matchco@example.com")
    proc = (await client.post(f"{BASE}/procedures", json={"name": "Dental implants", "category": "Dental", "idempotency_key": "p-dental"}, headers=_h(token))).json()["procedure"]
    verified = await _provider(client, token, "Verified Dental")
    unverified = await _provider(client, token, "Unverified Dental", verified=False)
    for p in (verified, unverified):
        await client.post(f"{BASE}/offerings", json={"provider_id": p["id"], "procedure_id": proc["id"]}, headers=_h(token))
    lead = await _patient(client, token)
    r = await client.post(f"{BASE}/patient-leads", json={"lead_id": lead, "procedure_id": proc["id"], "preferred_destination_country": "IN"}, headers=_h(token))
    assert r.status_code == 200, r.text
    matches = (await client.get(f"{BASE}/leads/{lead}/provider-matches", headers=_h(token))).json()
    assert [m["name"] for m in matches["matches"]] == ["Verified Dental"]


# ------------------------------------------------------------------------------ duplicate and retried sends


async def test_a_duplicate_send_transfers_the_data_once(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Dup Co", "dup@example.com")
    provider = await _provider(client, token)
    lead = await _patient(client, token)
    ref = await _approved(client, token, lead, provider["id"])
    first, second = await _send(client, token, ref), await _send(client, token, ref)
    assert (first.json()["newly_sent"], second.json()["newly_sent"]) == (True, False)
    assert len(await _deliveries(tid, ref)) == 1


async def test_two_sends_at_the_same_moment_still_transfer_once(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Race Co", "race@example.com")
    provider = await _provider(client, token)
    lead = await _patient(client, token)
    ref = await _approved(client, token, lead, provider["id"])
    results = await asyncio.gather(_send(client, token, ref), _send(client, token, ref))
    assert all(r.status_code == 200 for r in results)
    assert len(await _deliveries(tid, ref)) == 1


async def test_a_failed_transport_leaves_nothing_marked_sent_and_a_retry_delivers_exactly_once(client, halla, monkeypatch) -> None:  # noqa: F811
    token, tid = await _gated(client, "Retry Co", "retry@example.com")
    provider = await _provider(client, token)
    lead = await _patient(client, token)
    ref = await _approved(client, token, lead, provider["id"])

    class Down:
        name = "down"

        async def send(self, provider, payload):
            raise RuntimeError("clinic unreachable")

    monkeypatch.setattr(mt_referrals, "get_transport", lambda name=None: Down())
    failed = await _send(client, token, ref)
    assert failed.status_code == 502 and failed.json()["detail"]["error"] == "transport_failed"
    got = (await client.get(f"{BASE}/referrals/{ref}", headers=_h(token))).json()
    assert got["referral"]["status"] == "APPROVED" and got["referral"]["send_attempts"] == 1 and got["deliveries"] == []

    monkeypatch.undo()
    for _ in range(3):  # retries after recovery, including repeats
        assert (await _send(client, token, ref)).status_code == 200
    assert len(await _deliveries(tid, ref)) == 1
    assert (await client.get(f"{BASE}/referrals/{ref}", headers=_h(token))).json()["referral"]["status"] == "SENT"


async def test_only_the_approved_fields_are_ever_sent(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Fields Co", "fields@example.com")
    provider = await _provider(client, token)
    lead = await _patient(client, token)
    ref = await _approved(client, token, lead, provider["id"], fields=("name",))
    assert (await _send(client, token, ref)).status_code == 200
    (d,) = await _deliveries(tid, ref)
    assert d.fields_sent == ["name"] and set(d.payload) == {"reference", "name"}
    assert "phone" not in d.payload and "email" not in d.payload


async def test_a_non_sandbox_transport_fails_closed(client, halla) -> None:  # noqa: F811
    from app.core.config import get_settings

    token, tid = await _gated(client, "Closed Co", "closed@example.com")
    provider = await _provider(client, token)
    lead = await _patient(client, token)
    ref = await _approved(client, token, lead, provider["id"])
    s = get_settings()
    old, s.MT_REFERRAL_TRANSPORT = s.MT_REFERRAL_TRANSPORT, "email"
    try:
        r = await _send(client, token, ref)
    finally:
        s.MT_REFERRAL_TRANSPORT = old
    assert r.status_code == 503 and r.json()["detail"]["error"] == "transport_not_configured"
    assert await _deliveries(tid, ref) == []


async def test_erasing_a_patients_details_removes_the_payload_values_but_keeps_what_was_sent_and_when(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Erase Co", "eraseco@example.com")
    provider = await _provider(client, token)
    lead = await _patient(client, token)
    ref = await _approved(client, token, lead, provider["id"])
    assert (await _send(client, token, ref)).status_code == 200
    assert (await client.post(f"/api/v1/leads/{lead}/consent", json={"scopes": []}, headers=_h(token))).status_code == 200
    r = await client.post(f"/api/v1/business-builder/leads/{lead}/halla/erase-personal-data", headers=_h(token))
    assert r.status_code == 200 and r.json()["referral_payloads"] == 1
    (d,) = await _deliveries(tid, ref)
    assert d.payload is None and sorted(d.fields_sent) == ["email", "name", "phone"] and d.sent_at is not None


# ------------------------------------------------------------------------------ who may do what


def test_coordinators_work_cases_but_only_managers_verify_providers_and_move_money() -> None:
    assert role_has_permission(Role.STAFF, Permission.COORDINATE_MEDICAL_TOURISM) and not role_has_permission(Role.STAFF, Permission.MANAGE_MEDICAL_TOURISM)
    assert role_has_permission(Role.MANAGER, Permission.COORDINATE_MEDICAL_TOURISM) and role_has_permission(Role.MANAGER, Permission.MANAGE_MEDICAL_TOURISM)
    assert not role_has_permission(Role.READ_ONLY, Permission.COORDINATE_MEDICAL_TOURISM)
    assert not role_has_permission(Role.TECHNICIAN, Permission.COORDINATE_MEDICAL_TOURISM)


async def test_every_decision_leaves_an_audit_row(client, halla) -> None:  # noqa: F811
    from app.models.audit_log import AuditLog

    token, tid = await _gated(client, "Audit Co", "auditco@example.com")
    provider = await _provider(client, token)
    lead = await _patient(client, token)
    ref = await _approved(client, token, lead, provider["id"])
    await _send(client, token, ref)
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        actions = [a.action for a in (await s.execute(select(AuditLog).where(AuditLog.tenant_id == tid, AuditLog.entity_id == uuid.UUID(ref)))).scalars()]
    assert {"mt.referral.created", "mt.referral.approval_granted", "mt.referral.sent"} <= set(actions)


async def test_a_clinic_is_verified_through_credentials_an_owner_can_add_and_check(client, halla) -> None:  # noqa: F811
    """The owner's whole path in the product: add a credential (with its expiry), record that a person checked it, then mark the clinic verified."""
    token, tid = await _gated(client, "Cred Co", "credco@example.com")
    p = await _provider(client, token, "Unchecked Clinic", verified=False)
    refused = await client.put(f"{BASE}/providers/{p['id']}/verification", json={"status": "VERIFIED"}, headers=_h(token))
    assert refused.status_code in (409, 422)  # nothing checked yet: the gate holds
    expiry = (datetime.now(timezone.utc).date() + timedelta(days=200)).isoformat()
    c = (await client.post(f"{BASE}/providers/{p['id']}/credentials", json={"credential_type": "Operating licence", "issuing_authority": "Ministry of Health", "credential_number": "OL-1", "expiry_date": expiry}, headers=_h(token))).json()["credential"]
    assert c["status"] == "PENDING" and c["expiry_date"] == expiry and c["issued_date"] is None
    assert (await client.put(f"{BASE}/providers/{p['id']}/verification", json={"status": "VERIFIED"}, headers=_h(token))).status_code in (409, 422)  # added is not checked
    checked = (await client.post(f"{BASE}/providers/{p['id']}/credentials/{c['id']}/verify", headers=_h(token))).json()["credential"]
    assert checked["status"] == "VERIFIED" and checked["verified_at"]
    ok = await client.put(f"{BASE}/providers/{p['id']}/verification", json={"status": "VERIFIED"}, headers=_h(token))
    assert ok.status_code == 200
    listed = (await client.get(f"{BASE}/providers/{p['id']}/credentials", headers=_h(token))).json()["credentials"]
    assert [x["expiry_date"] for x in listed] == [expiry]


async def test_the_commission_desk_lists_commissions_and_referrals_still_waiting_for_one(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Desk Co", "deskco@example.com")
    provider = await _provider(client, token, "Desk Clinic")
    lead = await _patient(client, token)
    ref = await _approved(client, token, lead, provider["id"])
    await _send(client, token, ref)
    await client.post(f"{BASE}/referrals/{ref}/clinic-response", json={"accepted": True}, headers=_h(token))
    desk = (await client.get(f"{BASE}/commissions", headers=_h(token))).json()
    assert desk == {"commissions": [], "awaiting": []}  # no outcome yet: nothing is waiting for a commission
    await client.post(f"{BASE}/referrals/{ref}/outcome", json={"outcome": "TREATMENT_COMPLETED", "evidence": "clinic email"}, headers=_h(token))
    desk = (await client.get(f"{BASE}/commissions", headers=_h(token))).json()
    assert [(a["referral_id"], a["provider_name"], a["outcome_evidence"]) for a in desk["awaiting"]] == [(ref, "Desk Clinic", "clinic email")] and desk["commissions"] == []
    made = (await client.post(f"{BASE}/referrals/{ref}/commission", json={"currency": "usd", "basis": "FLAT", "flat_amount": "500"}, headers=_h(token))).json()["commission"]
    desk = (await client.get(f"{BASE}/commissions", headers=_h(token))).json()
    assert desk["awaiting"] == [] and [(c["id"], c["status"], c["lead_id"], c["provider_name"], c["outcome"]) for c in desk["commissions"]] == [(made["id"], "PENDING", lead, "Desk Clinic", "TREATMENT_COMPLETED")]
    other, _ = await _gated(client, "Desk Other", "deskother@example.com")
    assert (await client.get(f"{BASE}/commissions", headers=_h(other))).json() == {"commissions": [], "awaiting": []}  # tenant scoped
