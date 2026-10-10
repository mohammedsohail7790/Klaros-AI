"""Every way personal or health data can enter a consent-gated tenant (Medical Tourism), and the guarantee that the other tenants are untouched.

Covers: operator-created leads, the agent tool path, bulk import, PATCH, the unauthenticated public form, the patient-lead extension, existing leads
with no evidence, operator attestation / withdrawal, audit redaction and the erasure workflow. SQLite + in-process HTTP (not a deployed service).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.session import async_session_maker, set_tenant_context
from app.models.actor import ActorType
from app.services import consent_gate
from tests.test_business_journey_api import _register
from tests.test_halla_integration import _connected, _h, _lead, _tenant_id, halla  # noqa: F401
from tests.test_halla_pilot_startup import _profiled

ALL = ["contact", "store_personal_data", "store_medical_information"]
PII_NAME, PII_PHONE, PII_EMAIL, HEALTH = "Zed Quasar-Patient", "+15550177123", "zed.quasar@example.com", "knee replacement ZXQ-health"


async def _gated(client, name, email, *, wording=("owner-label-v1",)):
    token, tid = await _profiled(client, name, email, "medical_tourism")
    from app.api.tool_deps_business_builder import get_halla_integration_service

    conn = await get_halla_integration_service()._connections.get_connection(tid, "halla")
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        row = await s.get(type(conn), conn.id)
        row.connection_metadata = {**(row.connection_metadata or {}), **({"approved_wording_versions": list(wording)} if wording else {})}
        await s.commit()
    return token, tid


async def _plain(client, name, email):
    return await _register(client, name, email), None


def _body(**over):
    return {"name": PII_NAME, "source": "WEB", "phone": PII_PHONE, "email": PII_EMAIL, **over}


async def _leads(client, token):
    r = (await client.get("/api/v1/leads", headers=_h(token))).json()
    return r.get("leads") or []


async def _audit_text(tid):
    from app.models.audit_log import AuditLog

    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        return " ".join(str(a.input_summary) + str(a.action) for a in (await s.execute(select(AuditLog).where(AuditLog.tenant_id == tid))).scalars())


async def _evidence(tid):
    from app.models.halla_consent import HallaConsentEvidence

    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        return list((await s.execute(select(HallaConsentEvidence).where(HallaConsentEvidence.tenant_id == tid).order_by(HallaConsentEvidence.recorded_at))).scalars())


async def _enable_vertical(tid):
    from app.services.vertical_extension_service import VerticalExtensionService

    vx = VerticalExtensionService(async_session_maker)
    try:
        await vx.get_by_key("medical_tourism")
    except Exception:
        await vx.create_vertical(key="medical_tourism", name="Medical Tourism")
    await vx.enable_for_organization(tid, "medical_tourism")


# ----------------------------------------------------------------------------------------------- operator-created leads
async def test_operator_lead_without_consent_is_refused_stores_nothing_and_leaves_no_personal_data_in_the_audit_log(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Gate Op1", "gateop1@example.com")
    r = await client.post("/api/v1/leads", json=_body(), headers=_h(token))
    assert r.status_code == 422 and r.json()["detail"] == {"error": "consent_required", "missing_scopes": ["store_personal_data"]}
    assert await _leads(client, token) == []
    audit = await _audit_text(tid)
    assert "tool.execute:crm.create_lead" in audit and PII_NAME not in audit and PII_PHONE not in audit and PII_EMAIL not in audit and "***PII***" in audit
    assert PII_NAME not in r.text and PII_PHONE not in r.text


async def test_operator_lead_with_consent_is_created_with_attributed_evidence_and_a_redacted_audit_record(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Gate Op2", "gateop2@example.com")
    r = await client.post("/api/v1/leads", json=_body(consent={"scopes": ["store_personal_data"], "wording_version": "owner-label-v1"}), headers=_h(token))
    assert r.status_code == 201, r.text
    lead = r.json()["lead"]["id"]
    ev = await _evidence(tid)
    assert len(ev) == 1 and ev[0].source == "operator_attested" and ev[0].actor_user_id is not None and str(ev[0].lead_id) == lead and ev[0].scopes == ["store_personal_data"]
    assert PII_NAME not in await _audit_text(tid)
    view = (await client.get(f"/api/v1/business-builder/leads/{lead}/halla/consent", headers=_h(token))).json()
    assert (view["contact"], view["store_personal_data"], view["store_medical_information"]) == (False, True, False)


async def test_health_text_needs_the_medical_scope_on_create_and_on_patch(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Gate Op3", "gateop3@example.com")
    bad = await client.post("/api/v1/leads", json=_body(description=HEALTH, consent={"scopes": ["store_personal_data"]}), headers=_h(token))
    assert bad.status_code == 422 and bad.json()["detail"]["missing_scopes"] == ["store_medical_information"]
    svc = await client.post("/api/v1/leads", json=_body(service_requested="rhinoplasty", consent={"scopes": ["store_personal_data"]}), headers=_h(token))
    assert svc.status_code == 422
    ok = await client.post("/api/v1/leads", json=_body(description="planned consultation", consent={"scopes": ["store_personal_data", "store_medical_information"]}), headers=_h(token))
    assert ok.status_code == 201
    only_personal = (await client.post("/api/v1/leads", json=_body(name="Pat Two", phone="+15550177124", email="p2@example.com", consent={"scopes": ["store_personal_data"]}), headers=_h(token))).json()["lead"]["id"]
    patch = await client.patch(f"/api/v1/leads/{only_personal}", json={"description": HEALTH}, headers=_h(token))
    assert patch.status_code == 422                                                      # PATCH cannot add health text without the scope
    assert (await client.patch(f"/api/v1/leads/{only_personal}", json={"status": "CONTACTED"}, headers=_h(token))).status_code == 200  # non-personal changes still work
    assert HEALTH not in await _audit_text(tid)


async def test_an_agent_or_workflow_cannot_attest_consent(client, halla) -> None:  # noqa: F811
    uid = uuid.uuid4()
    raw = {"scopes": ALL, "wording_version": "x"}
    assert consent_gate.claim_from_tool(raw, ActorType.AI, uid) is None and consent_gate.claim_from_tool(raw, ActorType.WORKFLOW, uid) is None
    assert consent_gate.claim_from_tool(raw, ActorType.SYSTEM, None) is None and consent_gate.claim_from_tool(raw, ActorType.USER, None) is None
    assert consent_gate.claim_from_tool({"scopes": ["bogus"]}, ActorType.USER, uid) is None
    ok = consent_gate.claim_from_tool(raw, ActorType.USER, uid)
    assert ok.source == "operator_attested" and ok.scopes == frozenset(ALL) and ok.actor_user_id == uid
    # through the real service: a refused claim stores nothing
    from app.api.tool_deps import get_wired_event_bus
    from app.services.lead_service import CreateLeadInput, LeadService

    token, tid = await _gated(client, "Gate Op4", "gateop4@example.com")
    with pytest.raises(consent_gate.ConsentRequiredError):
        await LeadService(async_session_maker, get_wired_event_bus()).create_lead(tid, CreateLeadInput(name=PII_NAME, source="WEB", phone=PII_PHONE, consent=None))
    assert await _leads(client, token) == []


# ----------------------------------------------------------------------------------------------- import
async def test_bulk_import_is_refused_for_a_gated_tenant_and_unchanged_for_others(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Gate Imp", "gateimp@example.com")
    r = await client.post("/api/v1/leads/import", json=[{"name": PII_NAME, "phone": PII_PHONE}], headers=_h(token))
    assert r.status_code == 422 and r.json()["detail"]["error"] == "bulk_import_disabled_without_per_person_consent"
    assert await _leads(client, token) == [] and PII_NAME not in await _audit_text(tid)
    other, _ = await _plain(client, "Plain Imp", "plainimp@example.com")
    ok = await client.post("/api/v1/leads/import", json=[{"name": "Plain Person", "phone": "+15550100001"}], headers=_h(other))
    assert ok.status_code == 201 and ok.json()["created_count"] == 1


# ----------------------------------------------------------------------------------------------- attestation, withdrawal, freeze
async def test_attestation_withdrawal_freezes_the_lead_and_blocks_contact_and_sync(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Gate Att", "gateatt@example.com")
    lead = (await client.post("/api/v1/leads", json=_body(consent={"scopes": ALL}), headers=_h(token))).json()["lead"]["id"]
    BASE = "/api/v1/business-builder"
    assert (await client.post(f"{BASE}/leads/{lead}/halla/call", headers=_h(token))).status_code == 200   # contact consented
    assert (await client.post(f"/api/v1/leads/{lead}/consent", json={"scopes": ["bogus"]}, headers=_h(token))).status_code == 422
    r = await client.post(f"/api/v1/leads/{lead}/consent", json={"scopes": ["store_personal_data"], "wording_version": "v1"}, headers=_h(token))
    assert r.status_code == 200 and r.json()["contact"] is False and r.json()["store_personal_data"] is True
    assert (await client.post(f"{BASE}/leads/{lead}/halla/call", headers=_h(token))).status_code == 422
    assert (await client.post(f"{BASE}/leads/{lead}/halla/sync", headers=_h(token))).status_code == 422   # no contact consent => not sent to Halla
    r = await client.post(f"/api/v1/leads/{lead}/consent", json={"scopes": []}, headers=_h(token))        # withdraw everything
    assert r.json()["granted_scopes"] == [] and r.json()["history_count"] == 3
    erase = await client.post(f"{BASE}/leads/{lead}/halla/erase-personal-data", headers=_h(token))
    assert erase.status_code == 200
    audit = await _audit_text(tid)
    assert "lead.consent_recorded" in audit and "lead.personal_data_erased" in audit and PII_NAME not in audit and PII_PHONE not in audit
    assert (await client.post(f"/api/v1/leads/{lead}/consent", json={"scopes": []}, headers={})).status_code in (401, 403)


async def test_attestation_is_refused_for_other_tenants_unknown_leads_and_non_gated_tenants(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Gate Att2", "gateatt2@example.com")
    token2, _ = await _gated(client, "Gate Att3", "gateatt3@example.com")
    lead = (await client.post("/api/v1/leads", json=_body(consent={"scopes": ALL}), headers=_h(token))).json()["lead"]["id"]
    assert (await client.post(f"/api/v1/leads/{lead}/consent", json={"scopes": ALL}, headers=_h(token2))).status_code == 404
    assert (await client.post(f"/api/v1/leads/{uuid.uuid4()}/consent", json={"scopes": ALL}, headers=_h(token))).status_code == 404
    plain, _ = await _plain(client, "Plain Att", "plainatt@example.com")
    pl = (await client.post("/api/v1/leads", json={"name": "Plain", "source": "WEB"}, headers=_h(plain))).json()["lead"]["id"]
    assert (await client.post(f"/api/v1/leads/{pl}/consent", json={"scopes": ALL}, headers=_h(plain))).status_code == 409


# ----------------------------------------------------------------------------------------------- public (unauthenticated) form
async def test_public_form_is_closed_until_the_owner_approves_wording_then_requires_explicit_consent(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Gate Pub", "gatepub@example.com", wording=())
    url = f"/api/v1/public/leads/{tid}"
    r = await client.post(url, json={"name": PII_NAME, "source": "WEB", "email": PII_EMAIL, "consent": {"scopes": ALL, "wording_version": "owner-label-v1"}})
    assert r.status_code == 403 and r.json()["detail"]["error"] == "public_intake_closed"        # no approved wording => closed, even with a "consent" claim
    assert await _leads(client, token) == []

    token, tid = await _gated(client, "Gate Pub2", "gatepub2@example.com")                       # wording approved
    url = f"/api/v1/public/leads/{tid}"
    base = {"name": PII_NAME, "source": "WEB", "email": PII_EMAIL}
    assert (await client.post(url, json=base)).status_code == 422                                 # no consent object
    assert (await client.post(url, json={**base, "consent": {"scopes": ALL, "wording_version": "made-up"}})).status_code == 422   # unapproved wording
    assert (await client.post(url, json={**base, "consent": {"scopes": ["contact"], "wording_version": "owner-label-v1"}})).status_code == 422  # contact is not storage consent
    assert (await client.post(url, json={**base, "medical_history_summary": HEALTH, "consent": {"scopes": ["store_personal_data"], "wording_version": "owner-label-v1"}})).status_code == 422
    assert (await client.post(url, json={**base, "description": HEALTH, "consent": {"scopes": ["store_personal_data"], "wording_version": "owner-label-v1"}})).status_code == 422
    assert await _leads(client, token) == [] and await _evidence(tid) == []
    ok = await client.post(url, json={**base, "consent": {"scopes": ["store_personal_data"], "wording_version": "owner-label-v1"}})
    assert ok.status_code == 201 and ok.json()["lead_id"]
    ev = await _evidence(tid)
    assert len(ev) == 1 and ev[0].source == "web_form" and ev[0].actor_user_id is None and ev[0].wording_version == "owner-label-v1"
    assert (await client.post(url, json={**base, "website": "bot", "consent": {"scopes": ALL, "wording_version": "owner-label-v1"}})).json()["lead_id"] is None   # honeypot unchanged


async def test_public_form_stays_open_and_generic_for_other_tenants(client, halla) -> None:  # noqa: F811
    token, tid = await _register(client, "Plain Pub", "plainpub@example.com"), None
    tid = await _tenant_id(client, token)
    r = await client.post(f"/api/v1/public/leads/{tid}", json={"name": "Generic Visitor", "source": "WEB", "email": "gv@example.com"})
    assert r.status_code == 201 and r.json()["lead_id"] and await _evidence(tid) == []


async def test_a_vertical_enabled_tenant_without_a_halla_connection_is_still_gated(client, halla) -> None:  # noqa: F811
    token = await _register(client, "Vert Only", "vertonly@example.com")
    tid = await _tenant_id(client, token)
    await _enable_vertical(tid)
    assert (await client.post("/api/v1/leads", json=_body(), headers=_h(token))).status_code == 422
    r = await client.post(f"/api/v1/public/leads/{tid}", json={"name": PII_NAME, "source": "WEB", "email": PII_EMAIL, "consent": {"scopes": ALL, "wording_version": "x"}})
    assert r.status_code == 403                                                                   # no connection => no approved wording => closed
    assert (await client.post("/api/v1/leads/import", json=[{"name": PII_NAME}], headers=_h(token))).status_code == 422
    assert await _leads(client, token) == []


# ----------------------------------------------------------------------------------------------- patient-lead extension, existing leads
async def test_patient_lead_needs_evidence_on_the_existing_lead_including_legacy_leads(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Gate PL", "gatepl@example.com")
    await _enable_vertical(tid)
    # a legacy lead that predates the gate (inserted directly: it has no evidence at all)
    from app.models.crm import Lead

    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        legacy = Lead(tenant_id=tid, name="Legacy Person", source="WEB", status="NEW", qualification_status="PENDING", urgency="MEDIUM")
        s.add(legacy)
        await s.commit()
        legacy_id = str(legacy.id)
    MT = "/api/v1/medical-tourism/patient-leads"
    r = await client.post(MT, json={"lead_id": legacy_id, "preferred_destination_country": "TR"}, headers=_h(token))
    assert r.status_code == 422 and r.json()["detail"]["missing_scopes"] == ["store_personal_data"]
    await client.post(f"/api/v1/leads/{legacy_id}/consent", json={"scopes": ["store_personal_data"], "wording_version": "v1"}, headers=_h(token))
    assert (await client.post(MT, json={"lead_id": legacy_id, "medical_history_summary": HEALTH}, headers=_h(token))).status_code == 422   # health text: medical scope
    ok = await client.post(MT, json={"lead_id": legacy_id, "preferred_destination_country": "TR"}, headers=_h(token))
    assert ok.status_code == 200, ok.text


async def test_non_gated_tenants_keep_working_exactly_as_before(client, halla) -> None:  # noqa: F811
    token, _ = await _plain(client, "Plain All", "plainall@example.com")
    tid = await _tenant_id(client, token)
    r = await client.post("/api/v1/leads", json=_body(description=HEALTH, service_requested="x"), headers=_h(token))
    assert r.status_code == 201
    lead = r.json()["lead"]["id"]
    assert (await client.patch(f"/api/v1/leads/{lead}", json={"description": "more"}, headers=_h(token))).status_code == 200
    assert (await client.post("/api/v1/leads/import", json=[{"name": "A B"}], headers=_h(token))).status_code == 201
    assert await _evidence(tid) == [] and PII_NAME in await _audit_text(tid)   # their audit log is unchanged (not redacted)


# ----------------------------------------------------------------------------------------------- erasure workflow
async def test_erasure_scrubs_every_traced_copy_keeps_referenced_customers_and_audits_counts_only(client, halla) -> None:  # noqa: F811
    from app.models.audit_log import AuditLog
    from app.models.crm import Appointment, Customer, Lead
    from app.models.event import Event
    from app.models.medical_tourism import PatientLead
    from app.models.voice import CallSession

    token, tid = await _gated(client, "Gate Erase", "gateerase@example.com")
    await _enable_vertical(tid)
    lead = (await client.post("/api/v1/leads", json=_body(consent={"scopes": ALL}), headers=_h(token))).json()["lead"]["id"]
    lid = uuid.UUID(lead)
    assert (await client.post("/api/v1/medical-tourism/patient-leads", json={"lead_id": lead, "medical_history_summary": HEALTH, "insurance_notes": "ZXQ-ins"}, headers=_h(token))).status_code == 200
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        cust = Customer(tenant_id=tid, name=PII_NAME, email=PII_EMAIL, phone=PII_PHONE, phone_normalized=PII_PHONE, status="ACTIVE", notes="ZXQ-note")
        s.add(cust)
        await s.flush()
        row = await s.get(Lead, lid)
        row.customer_id = cust.id
        now = datetime.now(timezone.utc)
        s.add(Appointment(tenant_id=tid, lead_id=lid, customer_id=cust.id, title=f"Consult — {PII_NAME}", service="knee", location="Clinic A", start_time=now, end_time=now + timedelta(hours=1), status="CONFIRMED", notes="ZXQ-appt-note"))
        s.add(CallSession(tenant_id=tid, provider="twilio", external_call_id="CAx", direction="INBOUND", caller_number=PII_PHONE, status="COMPLETED", started_at=now, lead_id=lid, transcript=[{"t": HEALTH}], engine_state={"k": HEALTH}))
        s.add(Event(tenant_id=tid, event_type="halla.interaction.completed", source="halla", entity_type="lead", entity_id=lid, payload={"summary": HEALTH, "outcome": "ZXQ-out", "category": "keep"}, correlation_id=uuid.uuid4()))
        await s.commit()
        cust_id = cust.id
    assert (await client.post(f"/api/v1/business-builder/leads/{lead}/halla/erase-personal-data", headers=_h(token))).status_code == 409   # consent still granted
    await client.post(f"/api/v1/leads/{lead}/consent", json={"scopes": []}, headers=_h(token))
    r = await client.post(f"/api/v1/business-builder/leads/{lead}/halla/erase-personal-data", headers=_h(token))
    assert r.status_code == 200 and r.json() == {"erased": True, "lead": 1, "appointments": 1, "patient_leads": 1, "call_sessions": 1, "events_scrubbed": 1, "customer": "erased"}
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        blob = " ".join(str(vars(x)) for x in [await s.get(Lead, lid), await s.get(Customer, cust_id)])
        for m in (Appointment, PatientLead, CallSession, Event):
            blob += " ".join(str(vars(x)) for x in (await s.execute(select(m).where(m.tenant_id == tid))).scalars() if "halla" in str(getattr(x, "event_type", "halla")) or not hasattr(x, "event_type"))
        for secret in (PII_NAME, PII_PHONE, PII_EMAIL, "ZXQ-health", "ZXQ-note", "ZXQ-appt-note", "ZXQ-out", "ZXQ-ins"):
            assert secret not in blob, secret
        assert "Appointment (erased)" in blob and "keep" in blob                            # ids / categories survive
        audit = [a for a in (await s.execute(select(AuditLog).where(AuditLog.tenant_id == tid, AuditLog.action == "lead.personal_data_erased"))).scalars()]
        assert len(audit) == 1 and audit[0].actor_id is not None and "Quasar" not in str(audit[0].input_summary) and audit[0].input_summary["appointments"] == 1
    assert len(await _evidence(tid)) >= 2                                                    # the consent history is retained


async def test_a_customer_referenced_elsewhere_is_kept_and_the_reason_is_reported(client, halla) -> None:  # noqa: F811
    from app.models.crm import Customer, Lead

    token, tid = await _gated(client, "Gate Erase2", "gateerase2@example.com")
    lead = (await client.post("/api/v1/leads", json=_body(consent={"scopes": ALL}), headers=_h(token))).json()["lead"]["id"]
    lid = uuid.UUID(lead)
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        cust = Customer(tenant_id=tid, name=PII_NAME, email=PII_EMAIL, status="ACTIVE")
        s.add(cust)
        await s.flush()
        (await s.get(Lead, lid)).customer_id = cust.id
        other = Lead(tenant_id=tid, name="Other Lead", source="WEB", status="NEW", qualification_status="PENDING", urgency="MEDIUM", customer_id=cust.id)
        s.add(other)
        await s.commit()
        cust_id = cust.id
    await client.post(f"/api/v1/leads/{lead}/consent", json={"scopes": []}, headers=_h(token))
    r = (await client.post(f"/api/v1/business-builder/leads/{lead}/halla/erase-personal-data", headers=_h(token))).json()
    assert r["customer"] == "kept" and r["customer_kept_because_referenced_by"] == ["leads"]
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        assert (await s.get(Customer, cust_id)).name == PII_NAME            # an owner decision, not erased automatically


async def test_public_form_with_approved_wording_and_medical_consent_creates_lead_patient_lead_and_evidence(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Gate Pub3", "gatepub3@example.com")
    await _enable_vertical(tid)
    url = f"/api/v1/public/leads/{tid}"
    payload = {"name": PII_NAME, "source": "WEB", "email": PII_EMAIL, "preferred_destination_country": "tr", "medical_history_summary": HEALTH}
    consent = {"scopes": ["store_personal_data", "store_medical_information"], "wording_version": "owner-label-v1"}
    assert (await client.post(url, json={**payload, "consent": {"scopes": ["store_personal_data"], "wording_version": "owner-label-v1"}})).status_code == 422
    ok = await client.post(url, json={**payload, "consent": consent})
    assert ok.status_code == 201 and ok.json()["patient_lead_id"] is not None
    ev = await _evidence(tid)
    assert len(ev) == 1 and set(ev[0].scopes) == {"store_personal_data", "store_medical_information"} and ev[0].source == "web_form"
