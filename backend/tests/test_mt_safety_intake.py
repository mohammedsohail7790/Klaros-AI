"""Safety screening of text KLAROS receives about a Medical Tourism lead: the public form, lead create/update, patient-lead intake, consultation
and customer notes. A flagged case goes to a person (REQUIRES_HUMAN + one category-only event), is never auto-qualified, and no sensitive text is
copied into the event. Other tenants are untouched. SQLite + in-process HTTP; real service boundaries, no mocked classifier."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.session import async_session_maker, set_tenant_context
from app.models.actor import ActorType
from app.models.crm import Appointment, Customer, Lead, LeadStatus, QualificationStatus
from app.models.event import Event
from app.models.rbac import Role
from app.tools.base import ExecutionContext
from tests.test_consent_gate_intake import ALL, PII_EMAIL, PII_NAME, PII_PHONE, _body, _gated, _plain
from tests.test_halla_integration import _h, _tenant_id, halla  # noqa: F401

EMERGENCY = "I have chest pain and cannot breathe right now"
DIAGNOSIS = "Please diagnose my knee, do I have arthritis?"
PRESCRIPTION = "Which medication should I take and what dose?"
GUARANTEE = "I need a guarantee that the surgery works"
NORMAL = "I am interested in a dental implant consultation next spring"


def _ctx(tid, actor=ActorType.USER, role=Role.OWNER):
    return ExecutionContext(tenant_id=tid, actor_type=actor, actor_id=uuid.uuid4(), role=role)


async def _lead(tid, lead_id) -> Lead:
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        return await s.get(Lead, uuid.UUID(str(lead_id)))


async def _safety_events(tid) -> list[Event]:
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        return list((await s.execute(select(Event).where(Event.tenant_id == tid, Event.event_type == "lead.safety_escalated"))).scalars())


def _assert_no_text(events: list[Event], *needles: str) -> None:
    blob = " ".join(str(e.payload) for e in events)
    for n in needles:
        assert n not in blob, n


# ---------------------------------------------------------------------------------------------------- lead creation (API + tool path)
@pytest.mark.parametrize("text,category", [(EMERGENCY, "EMERGENCY"), (DIAGNOSIS, "DIAGNOSIS_REQUEST"), (PRESCRIPTION, "PRESCRIPTION_REQUEST"), (GUARANTEE, "OUTCOME_GUARANTEE_REQUEST")])
async def test_a_risky_lead_description_goes_to_a_person_with_the_category_only(client, halla, text, category) -> None:  # noqa: F811
    token, tid = await _gated(client, f"Safe {category[:5]}", f"safe{category[:4].lower()}@example.com")
    r = await client.post("/api/v1/leads", json=_body(description=text, consent={"scopes": ALL}), headers=_h(token))
    assert r.status_code == 201, r.text
    lead = await _lead(tid, r.json()["lead"]["id"])
    assert lead.qualification_status == QualificationStatus.REQUIRES_HUMAN
    assert lead.status in (LeadStatus.NEW, LeadStatus.CONTACTED)         # core lead-status semantics are untouched
    ev = await _safety_events(tid)
    assert len(ev) == 1 and ev[0].payload["category"] == category and ev[0].payload["origin"] == "lead_create"
    _assert_no_text(ev, text, "chest", "knee", PII_NAME, PII_EMAIL, PII_PHONE)


async def test_a_normal_inquiry_is_not_flagged(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Safe Normal", "safenormal@example.com")
    r = await client.post("/api/v1/leads", json=_body(description=NORMAL, consent={"scopes": ALL}), headers=_h(token))
    assert r.status_code == 201
    assert (await _lead(tid, r.json()["lead"]["id"])).qualification_status != QualificationStatus.REQUIRES_HUMAN
    assert await _safety_events(tid) == []


async def test_other_tenants_are_not_screened(client, halla) -> None:  # noqa: F811
    token, _ = await _plain(client, "Safe Plain", "safeplain@example.com")
    tid = await _tenant_id(client, token)
    r = await client.post("/api/v1/leads", json={"name": "Plain Person", "source": "WEB", "email": "pp@example.com", "description": EMERGENCY}, headers=_h(token))
    assert r.status_code == 201
    assert (await _lead(tid, r.json()["lead"]["id"])).qualification_status != QualificationStatus.REQUIRES_HUMAN
    assert await _safety_events(tid) == []


# ---------------------------------------------------------------------------------------------------- never auto-qualified
async def test_a_flagged_lead_is_not_qualified_by_a_machine_but_a_person_can_decide(client, halla, tool_registry) -> None:  # noqa: F811
    token, tid = await _gated(client, "Safe Qualify", "safequalify@example.com")
    r = await client.post("/api/v1/leads", json=_body(description=EMERGENCY, consent={"scopes": ALL}), headers=_h(token))
    lead_id = r.json()["lead"]["id"]
    for actor in (ActorType.AI, ActorType.WORKFLOW, ActorType.SYSTEM):
        out = await tool_registry.execute("crm.qualify_lead", {"lead_id": lead_id}, _ctx(tid, actor))
        assert out.qualification_status == QualificationStatus.REQUIRES_HUMAN
        with pytest.raises(ValueError):
            await tool_registry.execute("crm.update_lead", {"lead_id": lead_id, "status": "QUALIFIED"}, _ctx(tid, actor))
    lead = await _lead(tid, lead_id)
    assert lead.qualification_status == QualificationStatus.REQUIRES_HUMAN and lead.status != LeadStatus.QUALIFIED
    # a signed-in person can act on it
    done = await tool_registry.execute("crm.update_lead", {"lead_id": lead_id, "status": "CONTACTED"}, _ctx(tid, ActorType.USER))
    assert done.lead["status"] == "CONTACTED"
    again = await tool_registry.execute("crm.qualify_lead", {"lead_id": lead_id}, _ctx(tid, ActorType.USER))
    assert again.qualification_status != QualificationStatus.REQUIRES_HUMAN or again.score is not None


async def test_the_automatic_qualification_that_follows_creation_does_not_override_the_flag(client, halla, event_bus) -> None:  # noqa: F811
    token, tid = await _gated(client, "Safe Auto", "safeauto@example.com")
    r = await client.post("/api/v1/leads", json=_body(description=EMERGENCY, service_requested="implants", consent={"scopes": ALL}), headers=_h(token))
    # whatever asynchronous handlers ran after the created event, the flag is still set
    assert (await _lead(tid, r.json()["lead"]["id"])).qualification_status == QualificationStatus.REQUIRES_HUMAN


# ---------------------------------------------------------------------------------------------------- edits, public form, patient lead, notes, consultation
async def test_a_later_edit_that_becomes_risky_is_flagged_once(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Safe Edit", "safeedit@example.com")
    lead_id = (await client.post("/api/v1/leads", json=_body(description=NORMAL, consent={"scopes": ALL}), headers=_h(token))).json()["lead"]["id"]
    assert await _safety_events(tid) == []
    for _ in range(2):                                                    # the same risky edit twice -> one escalation
        r = await client.patch(f"/api/v1/leads/{lead_id}", json={"description": PRESCRIPTION}, headers=_h(token))
        assert r.status_code == 200, r.text
    ev = await _safety_events(tid)
    assert len(ev) == 1 and ev[0].payload["category"] == "PRESCRIPTION_REQUEST" and ev[0].payload["origin"] == "lead_update"
    assert (await _lead(tid, lead_id)).qualification_status == QualificationStatus.REQUIRES_HUMAN
    _assert_no_text(ev, PRESCRIPTION, "medication")


async def test_the_public_form_screens_its_text(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Safe Public", "safepublic@example.com")
    consent = {"scopes": ["store_personal_data", "store_medical_information"], "wording_version": "owner-label-v1"}
    r = await client.post(f"/api/v1/public/leads/{tid}", json={"name": PII_NAME, "source": "WEB", "email": PII_EMAIL, "description": GUARANTEE, "consent": consent})
    assert r.status_code == 201, r.text
    assert (await _lead(tid, r.json()["lead_id"])).qualification_status == QualificationStatus.REQUIRES_HUMAN
    ev = await _safety_events(tid)
    assert len(ev) == 1 and ev[0].payload["category"] == "OUTCOME_GUARANTEE_REQUEST"
    _assert_no_text(ev, GUARANTEE, PII_EMAIL)


async def test_patient_lead_health_text_is_screened(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Safe Patient", "safepatient@example.com")
    lead_id = (await client.post("/api/v1/leads", json=_body(description=NORMAL, consent={"scopes": ALL}), headers=_h(token))).json()["lead"]["id"]
    r = await client.post("/api/v1/medical-tourism/patient-leads", json={"lead_id": lead_id, "medical_history_summary": EMERGENCY}, headers=_h(token))
    assert r.status_code in (200, 201), r.text
    assert (await _lead(tid, lead_id)).qualification_status == QualificationStatus.REQUIRES_HUMAN
    ev = await _safety_events(tid)
    assert len(ev) == 1 and ev[0].payload["origin"] == "patient_lead"
    _assert_no_text(ev, EMERGENCY, "chest")


async def test_customer_notes_and_consultation_notes_are_screened(client, halla, tool_registry) -> None:  # noqa: F811
    from app.models.medical_tourism import Provider
    from app.services.medical_tourism_service import MedicalTourismService

    token, tid = await _gated(client, "Safe Notes", "safenotes@example.com")
    lead_id = (await client.post("/api/v1/leads", json=_body(description=NORMAL, consent={"scopes": ALL}), headers=_h(token))).json()["lead"]["id"]
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        cust = Customer(tenant_id=tid, name="Linked Person", status="ACTIVE")
        s.add(cust)
        await s.flush()
        (await s.get(Lead, uuid.UUID(lead_id))).customer_id = cust.id
        prov = Provider(tenant_id=tid, name="Synthetic Test Clinic", country="TR")
        s.add(prov)
        await s.flush()
        now = datetime.now(timezone.utc)
        appt = Appointment(tenant_id=tid, lead_id=uuid.UUID(lead_id), customer_id=cust.id, title="Consult", start_time=now + timedelta(days=3), end_time=now + timedelta(days=3, hours=1), status="SCHEDULED")
        s.add(appt)
        await s.commit()
        cust_id, prov_id, appt_id = cust.id, prov.id, appt.id
    await tool_registry.execute("crm.create_note", {"customer_id": str(cust_id), "body": DIAGNOSIS}, _ctx(tid))
    assert (await _lead(tid, lead_id)).qualification_status == QualificationStatus.REQUIRES_HUMAN
    ev = await _safety_events(tid)
    assert [e.payload["origin"] for e in ev] == ["customer_note"] and ev[0].payload["category"] == "DIAGNOSIS_REQUEST"

    # consultation notes: a second lead/appointment so the earlier flag does not mask the result
    lead2 = (await client.post("/api/v1/leads", json=_body(name="Second Person", email="second.person@example.com", phone="+15550909090", description=NORMAL, consent={"scopes": ALL}), headers=_h(token))).json()["lead"]["id"]
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        now = datetime.now(timezone.utc)
        a2 = Appointment(tenant_id=tid, lead_id=uuid.UUID(lead2), customer_id=cust_id, title="Consult 2", start_time=now + timedelta(days=4), end_time=now + timedelta(days=4, hours=1), status="SCHEDULED")
        s.add(a2)
        await s.commit()
        a2_id = a2.id
    svc = MedicalTourismService(async_session_maker)
    await svc.create_consultation(tid, a2_id, prov_id, notes=EMERGENCY)
    assert (await _lead(tid, lead2)).qualification_status == QualificationStatus.REQUIRES_HUMAN
    assert {e.payload["origin"] for e in await _safety_events(tid)} == {"customer_note", "consultation"}
    _assert_no_text(await _safety_events(tid), DIAGNOSIS, EMERGENCY, "knee", "chest")


# ---------------------------------------------------------------------------------------------------- fail safe
async def test_a_classifier_failure_flags_a_medical_tourism_lead_instead_of_passing_it(client, halla, monkeypatch) -> None:  # noqa: F811
    from app.services import pilot_safety

    token, tid = await _gated(client, "Safe Fail", "safefail@example.com")

    def boom(*a, **k):
        raise RuntimeError("classifier down")

    monkeypatch.setattr(pilot_safety, "assess", boom)
    r = await client.post("/api/v1/leads", json=_body(description=NORMAL, consent={"scopes": ALL}), headers=_h(token))
    assert r.status_code == 201                                           # the write still succeeds
    assert (await _lead(tid, r.json()["lead"]["id"])).qualification_status == QualificationStatus.REQUIRES_HUMAN
    assert [e.payload["category"] for e in await _safety_events(tid)] == ["SAFETY_CHECK_FAILED"]
