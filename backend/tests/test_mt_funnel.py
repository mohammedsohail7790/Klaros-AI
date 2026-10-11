"""Medical Tourism funnel telemetry: counts only, strict stage definitions (a TENTATIVE appointment is never a booking, an escalation event is a request to a
person, not proof they acted), tenant-scoped, no personal data in the answer. SQLite + in-process HTTP."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.crm import Appointment, AppointmentStatus, Customer, Lead
from app.models.integration import WebhookEvent, WebhookProcessingStatus
from tests.test_consent_gate_intake import ALL, PII_EMAIL, PII_NAME, PII_PHONE, _body, _gated, _plain
from tests.test_halla_integration import _h, _tenant_id, halla  # noqa: F401


async def _appointment(tid, lead_id, status, *, halla_id=None, hours=48) -> None:
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        now = datetime.now(timezone.utc)
        cust = Customer(tenant_id=tid, name="Synthetic Funnel Customer", status="ACTIVE")
        s.add(cust)
        await s.flush()
        s.add(Appointment(tenant_id=tid, lead_id=uuid.UUID(lead_id), customer_id=cust.id, title="Synthetic consult", start_time=now + timedelta(hours=hours), end_time=now + timedelta(hours=hours + 1),
                          status=status, external_provider="halla" if halla_id else None, external_id=halla_id))
        await s.commit()


async def test_the_funnel_reports_each_stage_by_its_own_definition_and_nothing_personal(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Funnel A", "funnela@example.com")
    mk = lambda **k: client.post("/api/v1/leads", json=_body(consent={"scopes": ALL}, **k), headers=_h(token))
    normal = (await mk(name="Funnel One", email="funnel.one@example.com", phone="+15551110001", description="dental implants next spring")).json()["lead"]["id"]
    held = (await mk(name="Funnel Two", email="funnel.two@example.com", phone="+15551110002", description="I have chest pain right now")).json()["lead"]["id"]
    frozen = (await mk(name="Funnel Three", email="funnel.three@example.com", phone="+15551110003", description="hair transplant")).json()["lead"]["id"]
    await client.post(f"/api/v1/leads/{frozen}/consent", json={"scopes": []}, headers=_h(token))            # everything withdrawn -> frozen
    no_contact = (await mk(name="Funnel Four", email="funnel.four@example.com", phone="+15551110004", description="knee replacement")).json()["lead"]["id"]
    await client.post(f"/api/v1/leads/{no_contact}/consent", json={"scopes": ["store_personal_data"]}, headers=_h(token))  # contact withdrawn only

    await _appointment(tid, normal, AppointmentStatus.TENTATIVE)                  # a request, not a booking
    await _appointment(tid, no_contact, AppointmentStatus.CONFIRMED, halla_id="halla-appt-1")
    async with async_session_maker() as s:                                         # one stuck and one failed Halla event, one processed
        await set_tenant_context(s, tid)
        old = datetime.now(timezone.utc) - timedelta(seconds=get_settings().HALLA_WEBHOOK_LEASE_SECONDS + 120)
        for i, st in enumerate((WebhookProcessingStatus.RECEIVED, WebhookProcessingStatus.FAILED, WebhookProcessingStatus.PROCESSED)):
            s.add(WebhookEvent(provider="halla", external_event_id=f"{tid}:fx-{i}", event_type="lead.qualified", tenant_id=tid, status=st, raw_payload={"id": f"fx-{i}"}))
        await s.commit()
        await s.execute(update(WebhookEvent).where(WebhookEvent.external_event_id == f"{tid}:fx-0").values(updated_at=old))
        await s.commit()

    r = await client.get("/api/v1/medical-tourism/funnel", headers=_h(token))
    assert r.status_code == 200, r.text
    f = r.json()
    assert f["received"] == 4
    assert f["consent"]["no_evidence"] == 0 and f["consent"]["frozen"] == 1
    assert f["consent"]["contact_granted"] == 2                                    # normal + held still have contact; frozen and no_contact do not
    assert f["consent"]["store_personal_data_granted"] == 3 and f["consent"]["store_medical_information_granted"] == 2
    assert f["qualification"]["held_for_person"] >= 1 and f["qualification"]["qualified"] == 0
    assert f["appointments"]["tentative_not_a_booking"] == 1 and f["appointments"]["confirmed"] == 1 and f["appointments"]["confirmed_by_halla"] == 1
    assert f["escalations_requested"]["klaros_safety_screen"] == 1 and f["escalations_requested"]["halla"] == 0
    d = f["delivery"]
    assert (d["halla_events_processed"], d["halla_events_failed"], d["halla_events_stuck_past_lease"], d["halla_events_in_flight"]) == (1, 1, 1, 0)
    assert f["drop_off"]["frozen_after_withdrawal_or_decline"] == 1 and f["drop_off"]["held_for_person"] >= 1
    blob = json.dumps(f)
    for needle in ("Funnel", "funnel.one", "+1555111", "chest", "dental", PII_NAME, PII_EMAIL, PII_PHONE, normal, held):
        assert needle not in blob, needle
    assert "in-memory" in f["definitions"]                                       # the durability limit is stated, not implied


async def test_the_funnel_is_tenant_scoped_and_works_for_other_tenants(client, halla) -> None:  # noqa: F811
    token_a, tid_a = await _gated(client, "Funnel Iso A", "funnelisoa@example.com")
    await client.post("/api/v1/leads", json=_body(consent={"scopes": ALL}), headers=_h(token_a))
    token_b, _ = await _plain(client, "Funnel Iso B", "funnelisob@example.com")
    tid_b = await _tenant_id(client, token_b)
    for i in range(2):
        await client.post("/api/v1/leads", json={"name": f"Plain Person {i}", "source": "WEB", "email": f"pp{i}@example.com"}, headers=_h(token_b))
    a = (await client.get("/api/v1/medical-tourism/funnel", headers=_h(token_a))).json()
    b = (await client.get("/api/v1/medical-tourism/funnel", headers=_h(token_b))).json()
    assert a["received"] == 1 and b["received"] == 2                                # each sees only its own records
    assert b["consent"]["no_evidence"] == 2                                         # a tenant with no consent evidence is reported as such


async def test_the_funnel_needs_a_signed_in_user(client, halla) -> None:  # noqa: F811
    assert (await client.get("/api/v1/medical-tourism/funnel")).status_code in (401, 403)
