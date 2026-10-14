"""The Medical Tourism business, end to end, through the real application: a consented patient, a verified clinic, the patient's approval, the crew sending the referral and chasing the
clinic through the real approvals machinery, the clinic's answer, the reported outcome, and a commission that moves only on evidence.

What is real here: the HTTP API, the consent gate, the referral gate, the crew members as provisioned, the governed tools, approvals (including the rule that an agent cannot approve its own
request), the event bus, the audit log and the database. What is a stand-in: the outgoing e-mail channel (it records what would be sent, because no provider is configured in tests) and
the AI model (the crew's tool calls are made directly, in the order its instructions give, instead of being chosen by a model)."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.api.tool_deps import get_tool_registry
from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.event import Event, EventType
from app.models.medical_tourism import Provider
from app.models.medical_tourism_referral import ProviderReferral
from app.models.rbac import Role
from app.services import operating_crew as oc
from app.tools.base import ExecutionContext
from app.tools.errors import ToolApprovalRequiredError
from tests.test_approval_flow import _create_second_user
from tests.test_consent_gate_intake import _enable_vertical
from tests.test_mt_referrals import BASE, _approved, _deliveries, _FakeChannel, _gated, _h, _patient, _provider, halla  # noqa: F401

pytestmark = pytest.mark.asyncio


def _agent(tid) -> ExecutionContext:
    return ExecutionContext(tenant_id=tid, actor_type=ActorType.AI, actor_id=uuid.uuid4(), role=Role.MANAGER)


async def _ask_as_crew(tid, tool: str, payload: dict) -> uuid.UUID:
    """A crew member calls a tool that needs approval: nothing happens, an approval request is created."""
    with pytest.raises(ToolApprovalRequiredError) as pending:
        await get_tool_registry().execute(tool, payload, _agent(tid))
    return pending.value.approval_request_id


async def test_the_medical_tourism_business_runs_from_enquiry_to_paid_commission(client, halla, monkeypatch) -> None:  # noqa: F811
    s = get_settings()
    fake = _FakeChannel()
    monkeypatch.setattr(s, "MT_REFERRAL_TRANSPORT", "email")
    monkeypatch.setattr(s, "SENDGRID_API_KEY", "key-for-test")
    monkeypatch.setattr(s, "SENDGRID_FROM_EMAIL", "from@klaros.example")
    monkeypatch.setattr("app.communications.factory.get_clinic_channel", lambda session_factory: fake)

    # --- the business is set up: a tenant that handles patient data, with the Medical Tourism module and its whole crew started on its own
    token, tid = await _gated(client, "End To End Clinics", "e2e@example.com")
    await _enable_vertical(tid)
    started = await oc.ensure_crew(async_session_maker, tid, None)
    assert started is not None and not started.failed
    crew = {m["key"]: m for m in (await client.get("/api/v1/operating-crew", headers=_h(token))).json()["members"]}
    for member in ("clinic-matcher", "safety-triage", "clinic-sourcer", "referral-coordinator", "clinic-follow-up", "verification-assistant"):
        assert crew[member]["running"] is True, member

    # --- a verified clinic with a contact address, and a consented patient
    clinic = await _provider(client, token, "Bosphorus Dental")
    async with async_session_maker() as session:
        await set_tenant_context(session, tid)
        (await session.get(Provider, uuid.UUID(clinic["id"]))).contact_email = "intake@bosphorus.example"
        await session.commit()
    lead = await _patient(client, token)

    # --- a person records the PATIENT'S approval for this one clinic; Klaros announces it (ids only)
    referral = await _approved(client, token, lead, clinic["id"])
    async with async_session_maker() as session:
        await set_tenant_context(session, tid)
        announced = list((await session.execute(select(Event).where(Event.tenant_id == tid, Event.event_type == EventType.MT_REFERRAL_APPROVED))).scalars())
    assert len(announced) == 1 and str(announced[0].entity_id) == referral and set(announced[0].payload) == {"referral_id", "provider_id"}

    # --- the Referral Coordinator tries to send it: it cannot move patient data on its own, it asks, and nothing leaves
    approval_id = await _ask_as_crew(tid, "medical_tourism.send_referral", {"referral_id": referral})
    assert fake.sent == [] and await _deliveries(tid, referral) == []
    pending = (await client.get("/api/v1/approvals", headers=_h(token))).json()["approvals"]
    assert any(a["id"] == str(approval_id) and a["tool_name"] == "medical_tourism.send_referral" and a["requested_by_type"].upper() == "AI" for a in pending)

    # --- a person approves in the approvals page; the original action resumes and the clinic is told, with only the fields the patient approved
    owner_2 = await _create_second_user(async_session_maker, tid, role=Role.OWNER)
    decided = await client.post(f"/api/v1/approvals/{approval_id}/approve", json={}, headers={"Authorization": f"Bearer {owner_2}"})
    assert decided.status_code == 200 and decided.json()["execution_status"] == "EXECUTED"
    assert len(fake.sent) == 1 and fake.sent[0]["to"] == "intake@bosphorus.example"
    assert "Name:" in fake.sent[0]["body"] and "Phone:" in fake.sent[0]["body"] and f"Reference: {referral}" in fake.sent[0]["body"]
    assert len(await _deliveries(tid, referral)) == 1
    assert (await client.get(f"{BASE}/referrals/{referral}", headers=_h(token))).json()["referral"]["status"] == "SENT"

    # --- five days pass with no answer: the follow-up member sees it, and chases the clinic (again only with approval, with no patient data)
    async with async_session_maker() as session:
        await set_tenant_context(session, tid)
        (await session.get(ProviderReferral, uuid.UUID(referral))).sent_at = datetime.now(timezone.utc) - timedelta(days=5)
        await session.commit()
    work = await get_tool_registry().execute("medical_tourism.list_referral_work", {}, _agent(tid))
    assert [w["referral_id"] for w in work.awaiting_clinic_response] == [referral] and work.approved_not_sent == []
    reminder = await _ask_as_crew(tid, "medical_tourism.remind_clinic", {"referral_id": referral})
    assert len(fake.sent) == 1  # still waiting for a person
    assert (await client.post(f"/api/v1/approvals/{reminder}/approve", json={}, headers={"Authorization": f"Bearer {owner_2}"})).json()["execution_status"] == "EXECUTED"
    assert len(fake.sent) == 2 and fake.sent[1]["to"] == "intake@bosphorus.example"
    assert "Synthetic Patient" not in fake.sent[1]["body"] and "@example.com" not in fake.sent[1]["body"] and referral in fake.sent[1]["body"]
    again = await _ask_as_crew(tid, "medical_tourism.remind_clinic", {"referral_id": referral})
    await client.post(f"/api/v1/approvals/{again}/approve", json={}, headers={"Authorization": f"Bearer {owner_2}"})
    assert len(fake.sent) == 2  # a clinic is never chased twice in three days

    # --- the clinic answers, and its REPORTED outcome (with where it was reported) is what unlocks the commission
    assert (await client.post(f"{BASE}/referrals/{referral}/clinic-response", json={"accepted": True}, headers=_h(token))).json()["referral"]["status"] == "ACCEPTED"
    commission = (await client.post(f"{BASE}/referrals/{referral}/commission", json={"currency": "usd", "basis": "FLAT", "flat_amount": "500"}, headers=_h(token))).json()["commission"]
    assert (await client.post(f"{BASE}/commissions/{commission['id']}/transition", json={"status": "CONFIRMED"}, headers=_h(token))).status_code >= 400  # no outcome yet: still potential
    assert (await client.post(f"{BASE}/referrals/{referral}/outcome", json={"outcome": "TREATMENT_COMPLETED", "evidence": "clinic email, 30 Oct"}, headers=_h(token))).status_code == 200
    for step in ("CONFIRMED", "INVOICED", "PAID"):
        assert (await client.post(f"{BASE}/commissions/{commission['id']}/transition", json={"status": step}, headers=_h(token))).json()["commission"]["status"] == step

    # --- and every step left a trail
    async with async_session_maker() as session:
        await set_tenant_context(session, tid)
        actions = {a.action for a in (await session.execute(select(AuditLog).where(AuditLog.tenant_id == tid))).scalars()}
    assert {"mt.referral.sent", "mt.referral.reminder_sent"} <= actions
