"""Medical Tourism: the eight-stage pipeline, human handoff tasks, commission rules and the structured intake from Halla events.

Stage, task and intake behaviour is derived from recorded facts (consent evidence, qualification, referrals, signed Halla events); nothing here is typed in by hand.
SQLite + in-process HTTP and signed in-process webhook deliveries (not a deployed service).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.session import async_session_maker, set_tenant_context
from app.models.audit_log import AuditLog
from app.models.crm import Lead, QualificationStatus
from app.models.medical_tourism import PatientLead
from app.models.medical_tourism_referral import HandoffTask
from app.services import mt_pipeline
from app.services.mt_pipeline import Facts, derive_stage
from tests.test_halla_contract_mt import HTENANT, SECRET, _mt, _named
from tests.test_halla_integration import _body as halla_body, _deliver, _h, halla  # noqa: F401
from tests.test_mt_referrals import BASE, _approved, _gated, _patient, _provider, _referral, _send


def _facts(**kw) -> Facts:
    base = dict(has_evidence=True, personal_ok=True, qualification="QUALIFIED", referral_statuses=(), outcome_reported=False)
    return Facts(**{**base, **kw})


@pytest.mark.parametrize("facts,stage", [
    (_facts(has_evidence=False, qualification="PENDING"), "NEW_ENQUIRY"),
    (_facts(personal_ok=False, qualification="PENDING"), "CONSENT"),
    (_facts(qualification="PENDING"), "QUALIFICATION"),
    (_facts(qualification="REQUIRES_HUMAN"), "QUALIFICATION"),  # held for a person: not advanced by a machine
    (_facts(), "PROVIDER_OPTIONS"),
    (_facts(referral_statuses=("DRAFT",)), "PATIENT_APPROVAL"),
    (_facts(referral_statuses=("APPROVED",)), "PATIENT_APPROVAL"),  # approved but not yet sent is still the approval stage
    (_facts(referral_statuses=("SENT",)), "REFERRED"),
    (_facts(referral_statuses=("ACCEPTED",)), "CLINIC_RESPONSE"),
    (_facts(referral_statuses=("DECLINED",)), "CLINIC_RESPONSE"),
    (_facts(referral_statuses=("OUTCOME_REPORTED",)), "OUTCOME"),
    (_facts(referral_statuses=("SENT",), outcome_reported=True), "OUTCOME"),
    (_facts(referral_statuses=("CANCELLED",)), "PROVIDER_OPTIONS"),  # a cancelled referral does not advance the patient
])
def test_the_stage_is_derived_from_recorded_facts_and_cannot_be_skipped(facts, stage) -> None:
    assert derive_stage(facts) == stage


def test_there_are_exactly_eight_ordered_stages() -> None:
    assert mt_pipeline.STAGES == ("NEW_ENQUIRY", "CONSENT", "QUALIFICATION", "PROVIDER_OPTIONS", "PATIENT_APPROVAL", "REFERRED", "CLINIC_RESPONSE", "OUTCOME")


async def _qualify(tid, lead_id) -> None:
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        (await s.get(Lead, uuid.UUID(lead_id))).qualification_status = QualificationStatus.QUALIFIED
        await s.commit()


async def _stage(client, token, lead_id) -> str:
    pipe = (await client.get(f"{BASE}/pipeline", headers=_h(token))).json()
    return next(x["stage"] for x in pipe["leads"] if x["lead_id"] == lead_id)


async def test_a_patient_moves_through_the_stages_only_as_real_events_happen(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Stages Co", "stages@example.com")
    provider = await _provider(client, token)
    lead = await _patient(client, token)
    assert await _stage(client, token, lead) == "QUALIFICATION"  # consent captured, not yet qualified
    await _qualify(tid, lead)
    assert await _stage(client, token, lead) == "PROVIDER_OPTIONS"
    ref = await _referral(client, token, lead, provider["id"])
    assert await _stage(client, token, lead) == "PATIENT_APPROVAL"
    await client.post(f"{BASE}/referrals/{ref}/approval", json={"decision": "GRANTED", "permitted_fields": ["name"], "patient_informed": True, "wording_version": "SHARE-v1"}, headers=_h(token))
    assert await _stage(client, token, lead) == "PATIENT_APPROVAL"  # approved, nothing sent yet
    await _send(client, token, ref)
    assert await _stage(client, token, lead) == "REFERRED"
    await client.post(f"{BASE}/referrals/{ref}/clinic-response", json={"accepted": True}, headers=_h(token))
    assert await _stage(client, token, lead) == "CLINIC_RESPONSE"
    await client.post(f"{BASE}/referrals/{ref}/outcome", json={"outcome": "CONSULTATION_ATTENDED", "evidence": "clinic note"}, headers=_h(token))
    assert await _stage(client, token, lead) == "OUTCOME"
    counts = (await client.get(f"{BASE}/pipeline", headers=_h(token))).json()["counts"]
    assert counts["OUTCOME"] == 1 and sum(counts.values()) == 1


async def test_a_lead_whose_personal_data_is_not_consented_is_listed_without_a_name(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Frozen Co", "frozenco@example.com")
    lead = await _patient(client, token)
    await client.post(f"/api/v1/leads/{lead}/consent", json={"scopes": []}, headers=_h(token))  # the patient withdrew everything
    row = next(x for x in (await client.get(f"{BASE}/pipeline", headers=_h(token))).json()["leads"] if x["lead_id"] == lead)
    assert row["stage"] == "CONSENT" and row["consent_frozen"] is True and row["name"] is None


async def test_the_pipeline_is_tenant_scoped(client, halla) -> None:  # noqa: F811
    token_a, _ = await _gated(client, "Pipe A", "pipea@example.com")
    token_b, _ = await _gated(client, "Pipe B", "pipeb@example.com")
    lead = await _patient(client, token_a)
    assert [x["lead_id"] for x in (await client.get(f"{BASE}/pipeline", headers=_h(token_b))).json()["leads"]] == []
    assert any(x["lead_id"] == lead for x in (await client.get(f"{BASE}/pipeline", headers=_h(token_a))).json()["leads"])


# ------------------------------------------------------------------------------ Halla events -> leads, intake and handoff tasks


def _consent(scopes, granted=True) -> dict:
    return {"granted": granted, "scope": list(scopes), "method": "voice_ai_verbal", "wording_version": "owner-label-v1", "recorded_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()}


async def _halla(client, tid, etype, data, eid=None):
    return await _deliver(client, tid, halla_body(etype, tenant=HTENANT, data=data, eid=eid), secret=SECRET)


async def _patient_row(tid, lead_id):
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        return (await s.execute(select(PatientLead).where(PatientLead.tenant_id == tid, PatientLead.lead_id == uuid.UUID(lead_id)))).scalar_one_or_none()


INTAKE = {"procedure": "Dental implants", "destination": "India", "timeframe": "next spring", "preferredLanguage": "Arabic", "preferredContactTime": "evenings"}


async def _created(client, token, tid, scopes, *, intake=INTAKE, name=None):
    name = name or f"Intake Patient {uuid.uuid4().hex[:6]}"
    r = await _halla(client, tid, "lead.created", {"leadId": f"h-{uuid.uuid4().hex[:8]}", "name": name, "phone": f"+1555{uuid.uuid4().int % 10_000_000:07d}", "consent": _consent(scopes), "intake": intake})
    assert r.status_code == 200, r.text
    found = await _named(client, token, name)
    return found[0]["id"] if found else None


async def test_the_structured_intake_is_stored_and_the_treatment_only_with_medical_consent(client, halla) -> None:  # noqa: F811
    token, tid = await _mt(client, "Intake Co", "intakeco@example.com")
    full = await _created(client, token, tid, ["contact", "store_personal_data", "store_medical_information"])
    p = await _patient_row(tid, full)
    assert (p.preferred_destination_country, p.timeframe, p.preferred_language, p.preferred_contact_time, p.procedure_text) == ("IN", "next spring", "Arabic", "evenings", "Dental implants")

    personal_only = await _created(client, token, tid, ["contact", "store_personal_data"])
    p = await _patient_row(tid, personal_only)
    assert p.procedure_text is None and p.preferred_destination_country == "IN" and p.timeframe == "next spring"  # the treatment is health-related: dropped without its own consent


async def test_no_intake_is_stored_without_personal_data_consent(client, halla) -> None:  # noqa: F811
    token, tid = await _mt(client, "Intake None Co", "intakenone@example.com")
    lead = await _created(client, token, tid, ["contact"])  # no store_personal_data: Klaros creates no lead at all
    assert lead is None
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        assert (await s.execute(select(PatientLead).where(PatientLead.tenant_id == tid))).scalars().all() == []


async def test_unknown_or_oversized_intake_values_are_dropped_and_an_unresolvable_destination_is_kept_as_text(client, halla) -> None:  # noqa: F811
    token, tid = await _mt(client, "Intake Odd Co", "intakeodd@example.com")
    lead = await _created(client, token, tid, ["contact", "store_personal_data", "store_medical_information"],
                          intake={"procedure": "x" * 500, "destination": "somewhere warm", "diagnosis": "secret", "timeframe": "  soon  "})
    p = await _patient_row(tid, lead)
    assert len(p.procedure_text) == 80 and p.destination_text == "somewhere warm" and p.preferred_destination_country is None and p.timeframe == "soon"
    assert not hasattr(p, "diagnosis")


async def test_the_intake_lets_matching_find_a_verified_provider_for_the_enquiry(client, halla) -> None:  # noqa: F811
    token, tid = await _mt(client, "Intake Match Co", "intakematch@example.com")
    proc = (await client.post(f"{BASE}/procedures", json={"name": "Dental implants", "category": "Dental", "idempotency_key": "pd"}, headers=_h(token))).json()["procedure"]
    provider = await _provider(client, token, "Match Clinic")
    await client.post(f"{BASE}/offerings", json={"provider_id": provider["id"], "procedure_id": proc["id"]}, headers=_h(token))
    lead = await _created(client, token, tid, ["contact", "store_personal_data", "store_medical_information"])
    matches = (await client.get(f"{BASE}/leads/{lead}/provider-matches", headers=_h(token))).json()
    assert [m["name"] for m in matches["matches"]] == ["Match Clinic"] and matches["basis"]["procedure_source"] == "stated"


async def _tasks(tid, lead_id):
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        return list((await s.execute(select(HandoffTask).where(HandoffTask.tenant_id == tid, HandoffTask.lead_id == uuid.UUID(lead_id)))).scalars())


async def test_an_escalation_creates_one_task_per_call_even_when_the_event_is_redelivered(client, halla) -> None:  # noqa: F811
    token, tid = await _mt(client, "Task Co", "taskco@example.com")
    hid = f"h-{uuid.uuid4().hex[:8]}"
    name = f"Task Patient {uuid.uuid4().hex[:6]}"
    await _halla(client, tid, "lead.created", {"leadId": hid, "name": name, "phone": "+15550199001", "consent": _consent(["contact", "store_personal_data"])})
    lead = (await _named(client, token, name))[0]["id"]
    same_id = f"esc-{uuid.uuid4().hex[:8]}"
    for eid in (same_id, same_id, f"esc-{uuid.uuid4().hex[:8]}"):  # a redelivery and a second event for the SAME call
        assert (await _halla(client, tid, "lead.escalated", {"leadId": hid, "callId": "CA-ONE"}, eid=eid)).status_code == 200
    assert [(t.kind, t.status) for t in await _tasks(tid, lead)] == [("escalation", "OPEN")]
    await _halla(client, tid, "lead.escalated", {"leadId": hid, "callId": "CA-TWO"})  # a different call is a different escalation
    assert len(await _tasks(tid, lead)) == 2


async def test_a_task_has_one_owner_and_every_change_is_audited(client, halla) -> None:  # noqa: F811
    token, tid = await _mt(client, "Owner Co", "ownerco@example.com")
    hid, name = f"h-{uuid.uuid4().hex[:8]}", f"Owner Patient {uuid.uuid4().hex[:6]}"
    await _halla(client, tid, "lead.created", {"leadId": hid, "name": name, "phone": "+15550199002", "consent": _consent(["contact", "store_personal_data"])})
    lead = (await _named(client, token, name))[0]["id"]
    await _halla(client, tid, "lead.escalated", {"leadId": hid, "callId": "CA-OWN"})
    (task,) = (await client.get(f"{BASE}/tasks", params={"lead_id": lead}, headers=_h(token))).json()["tasks"]
    assert task["status"] == "OPEN" and task["owner_user_id"] is None

    assert (await client.post(f"{BASE}/tasks/{task['id']}/complete", json={}, headers=_h(token))).json()["detail"]["error"] == "not_accepted"  # cannot finish what nobody accepted
    accepted = (await client.post(f"{BASE}/tasks/{task['id']}/accept", headers=_h(token))).json()["task"]
    assert accepted["status"] == "ACCEPTED" and accepted["owner_user_id"]
    again = await client.post(f"{BASE}/tasks/{task['id']}/accept", headers=_h(token))
    assert again.status_code == 409 and again.json()["detail"]["error"] == "not_open"  # nobody silently takes over an owned task
    done = (await client.post(f"{BASE}/tasks/{task['id']}/complete", json={"note": "called the patient"}, headers=_h(token))).json()["task"]
    assert done["status"] == "DONE" and done["note"] == "called the patient" and done["completed_at"]
    assert (await client.post(f"{BASE}/tasks/{task['id']}/cancel", json={}, headers=_h(token))).json()["detail"]["error"] == "already_closed"

    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        actions = [a.action for a in (await s.execute(select(AuditLog).where(AuditLog.tenant_id == tid, AuditLog.entity_id == uuid.UUID(task["id"])).order_by(AuditLog.created_at))).scalars()]
    assert actions == ["mt.task.created", "mt.task.accepted", "mt.task.completed"]


async def test_a_qualified_lead_opens_a_provider_options_task_once(client, halla) -> None:  # noqa: F811
    token, tid = await _mt(client, "Qual Task Co", "qualtask@example.com")
    hid, name = f"h-{uuid.uuid4().hex[:8]}", f"Qual Patient {uuid.uuid4().hex[:6]}"
    await _halla(client, tid, "lead.created", {"leadId": hid, "name": name, "phone": "+15550199003", "consent": _consent(["contact", "store_personal_data"])})
    lead = (await _named(client, token, name))[0]["id"]
    for _ in range(2):
        await _halla(client, tid, "lead.qualified", {"leadId": hid, "callId": "CA-Q", "qualification": "qualified"})
    assert [t.kind for t in await _tasks(tid, lead)] == ["provider_options"]


# ------------------------------------------------------------------------------ commission rules


async def _commission(client, token, tid):
    provider = await _provider(client, token)
    lead = await _patient(client, token)
    ref = await _approved(client, token, lead, provider["id"])
    await _send(client, token, ref)
    await client.post(f"{BASE}/referrals/{ref}/clinic-response", json={"accepted": True}, headers=_h(token))
    c = (await client.post(f"{BASE}/referrals/{ref}/commission", json={"currency": "EUR", "basis": "PERCENTAGE", "commission_percentage": "10", "reference_amount": "4000"}, headers=_h(token))).json()["commission"]
    return ref, c


async def test_a_commission_moves_through_ordered_steps_and_is_not_revenue_until_evidence(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Comm Co", "commco@example.com")
    ref, c = await _commission(client, token, tid)
    assert c["computed_amount"] == "400.00" and c["status"] == "PENDING"
    rev = (await client.get(f"{BASE}/revenue", headers=_h(token))).json()["by_currency"]["EUR"]
    assert rev["PENDING"] == {"count": 1, "amount": "400.00"} and rev["CONFIRMED"]["count"] == 0 and rev["PAID"]["count"] == 0  # potential, not earned

    async def to(status):
        return await client.post(f"{BASE}/commissions/{c['id']}/transition", json={"status": status}, headers=_h(token))

    assert (await to("PAID")).json()["detail"]["error"] == "invalid_transition"  # cannot jump to paid
    assert (await to("INVOICED")).json()["detail"]["error"] == "invalid_transition"
    assert (await to("CONFIRMED")).json()["detail"]["error"] == "no_outcome_evidence"
    # an outcome that is NOT the one the agreement names is not enough
    await client.post(f"{BASE}/referrals/{ref}/outcome", json={"outcome": "CONSULTATION_ATTENDED", "evidence": "clinic note"}, headers=_h(token))
    assert (await to("CONFIRMED")).json()["detail"]["error"] == "no_outcome_evidence"
    await client.post(f"{BASE}/referrals/{ref}/outcome", json={"outcome": "TREATMENT_COMPLETED", "evidence": "clinic report #17"}, headers=_h(token))
    assert (await to("CONFIRMED")).status_code == 200 and (await to("CONFIRMED")).status_code == 200  # idempotent
    assert (await to("DISPUTED")).json()["commission"]["status"] == "DISPUTED"
    assert (await to("CONFIRMED")).json()["commission"]["status"] == "CONFIRMED"  # settled
    assert (await to("INVOICED")).json()["commission"]["invoiced_at"]
    assert (await to("PAID")).json()["commission"]["paid_at"]
    assert (await to("CANCELLED")).json()["detail"]["error"] == "invalid_transition"  # paid money is not cancelled; it can only be disputed
    rev = (await client.get(f"{BASE}/revenue", headers=_h(token))).json()["by_currency"]["EUR"]
    assert rev["PAID"] == {"count": 1, "amount": "400.00"} and rev["PENDING"]["count"] == 0


async def test_the_old_status_route_cannot_bypass_the_evidence_rule_for_a_provider_referral(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Bypass Co", "bypass@example.com")
    _, c = await _commission(client, token, tid)
    r = await client.patch(f"{BASE}/referral-commissions/{c['id']}/status", json={"status": "CONFIRMED"}, headers=_h(token))
    assert r.status_code == 409


async def test_revenue_never_mixes_currencies(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Currency Co", "currency@example.com")
    await _commission(client, token, tid)  # EUR
    provider = await _provider(client, token, "USD Clinic")
    lead = await _patient(client, token)
    ref = await _approved(client, token, lead, provider["id"])
    await client.post(f"{BASE}/referrals/{ref}/commission", json={"currency": "USD", "basis": "FLAT", "flat_amount": "250"}, headers=_h(token))
    by = (await client.get(f"{BASE}/revenue", headers=_h(token))).json()["by_currency"]
    assert set(by) == {"EUR", "USD"} and by["USD"]["PENDING"]["amount"] == "250.00" and by["EUR"]["PENDING"]["amount"] == "400.00"


async def test_the_pipeline_row_carries_the_enquiry_details_only_while_consent_allows_them(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Intake Rows Co", "introws@example.com")
    provider = await _provider(client, token, name="Row Clinic")
    lead = await _patient(client, token)
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        pl = PatientLead(tenant_id=tid, lead_id=uuid.UUID(lead))
        s.add(pl)
        pl.procedure_text, pl.destination_text, pl.timeframe, pl.preferred_language = "Dental implants", "Turkey", "Within 2 months", "Arabic"
        await s.commit()
    row = next(x for x in (await client.get(f"{BASE}/pipeline", headers=_h(token))).json()["leads"] if x["lead_id"] == lead)
    assert row["intake"] == {"procedure": "Dental implants", "destination": "Turkey", "timeframe": "Within 2 months", "language": "Arabic", "contact_time": None}
    ref = await _referral(client, token, lead, provider["id"])
    row = next(x for x in (await client.get(f"{BASE}/pipeline", headers=_h(token))).json()["leads"] if x["lead_id"] == lead)
    assert row["referrals"][0]["provider_name"] == "Row Clinic" and row["referrals"][0]["sent_at"] is None and row["referrals"][0]["id"] == ref
    # withdrawing personal-data consent removes every detail from the row, not just the name
    await client.post(f"/api/v1/leads/{lead}/consent", json={"scopes": []}, headers=_h(token))
    row = next(x for x in (await client.get(f"{BASE}/pipeline", headers=_h(token))).json()["leads"] if x["lead_id"] == lead)
    assert row["name"] is None and all(v is None for v in row["intake"].values())
    # medical consent alone controls the treatment
    lead2 = await _patient(client, token, scopes=["contact", "store_personal_data"])
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        pl = PatientLead(tenant_id=tid, lead_id=uuid.UUID(lead2))
        s.add(pl)
        pl.procedure_text, pl.destination_text = "Hair transplant", "Turkey"
        await s.commit()
    row2 = next(x for x in (await client.get(f"{BASE}/pipeline", headers=_h(token))).json()["leads"] if x["lead_id"] == lead2)
    assert row2["intake"]["procedure"] is None and row2["intake"]["destination"] == "Turkey"


async def test_a_treatment_typed_in_by_hand_shows_only_with_health_consent(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Manual Tx Co", "manualtx@example.com")
    with_health = await _patient(client, token)
    no_health = await _patient(client, token, scopes=["contact", "store_personal_data"])
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        for lid in (with_health, no_health):
            (await s.get(Lead, uuid.UUID(lid))).service_requested = "Dental implants"
        await s.commit()
    rows = {x["lead_id"]: x for x in (await client.get(f"{BASE}/pipeline", headers=_h(token))).json()["leads"]}
    assert rows[with_health]["intake"]["procedure"] == "Dental implants"
    assert rows[no_health]["intake"]["procedure"] is None
