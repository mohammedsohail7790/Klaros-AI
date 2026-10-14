"""The referral tools a crew member uses: they reach a clinic only through the same gate a coordinator's button uses, only with approval by default, and never with patient data in a reminder."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.api.tool_deps import get_tool_registry
from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.actor import ActorType
from app.models.medical_tourism import Provider
from app.models.medical_tourism_referral import ProviderReferral
from app.models.rbac import Role
from app.tools.base import ExecutionContext
from app.tools.errors import ToolApprovalRequiredError
from app.tools.policy import DEFAULT_TOOL_POLICIES, ActionPolicy
from tests.test_mt_referrals import _approved, _gated, _patient, _provider, _FakeChannel, halla  # noqa: F401

pytestmark = pytest.mark.asyncio


def _ctx(tid):
    return ExecutionContext(tenant_id=tid, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=Role.MANAGER)


async def _set(tid, referral_id, **fields):
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        row = await s.get(ProviderReferral, uuid.UUID(referral_id))
        for k, v in fields.items():
            setattr(row, k, v)
        await s.commit()


async def _set_provider(tid, provider_id, **fields):
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        row = await s.get(Provider, uuid.UUID(provider_id))
        for k, v in fields.items():
            setattr(row, k, v)
        await s.commit()


async def test_policies_sends_wait_for_approval_and_the_list_is_read_only() -> None:
    assert DEFAULT_TOOL_POLICIES["medical_tourism.list_referral_work"] == ActionPolicy.AUTO
    assert DEFAULT_TOOL_POLICIES["medical_tourism.send_referral"] == ActionPolicy.APPROVAL_REQUIRED
    assert DEFAULT_TOOL_POLICIES["medical_tourism.remind_clinic"] == ActionPolicy.APPROVAL_REQUIRED


async def test_an_unapproved_send_by_the_crew_sends_nothing(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Crew Send Co", "crewsend@example.com")
    provider = await _provider(client, token)
    ref = await _approved(client, token, await _patient(client, token), provider["id"])
    with pytest.raises(ToolApprovalRequiredError):
        await get_tool_registry().execute("medical_tourism.send_referral", {"referral_id": ref}, _ctx(tid))
    assert (await client.get(f"/api/v1/medical-tourism/referrals/{ref}", headers={"Authorization": f"Bearer {token}"})).json()["referral"]["status"] == "APPROVED"


async def test_an_approved_send_goes_through_the_same_gate_and_a_blocked_one_returns_the_reason(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Gate Send Co", "gatesend@example.com")
    good = await _approved(client, token, await _patient(client, token), (await _provider(client, token))["id"])
    out = await get_tool_registry().execute("medical_tourism.send_referral", {"referral_id": good}, _ctx(tid), skip_approval_gate=True)
    assert out.sent is True and out.newly_sent is True
    again = await get_tool_registry().execute("medical_tourism.send_referral", {"referral_id": good}, _ctx(tid), skip_approval_gate=True)
    assert again.sent is True and again.newly_sent is False  # never twice

    blocked = await _approved(client, token, await _patient(client, token), (await _provider(client, token, "Soon Unverified"))["id"])
    prov = (await client.get(f"/api/v1/medical-tourism/referrals/{blocked}", headers={"Authorization": f"Bearer {token}"})).json()["referral"]["provider_id"]
    await _set_provider(tid, prov, verification_status="SUSPENDED")  # the clinic stops being referable after the approval was recorded
    refused = await get_tool_registry().execute("medical_tourism.send_referral", {"referral_id": blocked}, _ctx(tid), skip_approval_gate=True)
    assert refused.sent is False and refused.reason


async def test_the_work_list_buckets_referrals_and_names_clinics_ready_for_verification(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Work Co", "workco@example.com")
    waiting = await _approved(client, token, await _patient(client, token), (await _provider(client, token, "Waiting Clinic"))["id"])
    await get_tool_registry().execute("medical_tourism.send_referral", {"referral_id": waiting}, _ctx(tid), skip_approval_gate=True)
    await _set(tid, waiting, sent_at=datetime.now(timezone.utc) - timedelta(days=5))
    fresh = await _approved(client, token, await _patient(client, token), (await _provider(client, token, "Fresh Clinic"))["id"])
    unsent = await _approved(client, token, await _patient(client, token), (await _provider(client, token, "Unsent Clinic"))["id"])
    await get_tool_registry().execute("medical_tourism.send_referral", {"referral_id": fresh}, _ctx(tid), skip_approval_gate=True)  # sent just now: not yet overdue

    work = await get_tool_registry().execute("medical_tourism.list_referral_work", {}, _ctx(tid))
    assert [w["referral_id"] for w in work.awaiting_clinic_response] == [waiting] and work.awaiting_clinic_response[0]["days_since_sent"] >= 5
    assert [w["referral_id"] for w in work.approved_not_sent] == [unsent]
    assert all("name" not in w and "phone" not in w for w in work.awaiting_clinic_response + work.approved_not_sent)  # no patient data
    assert work.ready_for_verification == []
    # an UNVERIFIED clinic that has a verified credential on record is ready for a person to verify
    p = await _provider(client, token, "Almost Verified", verified=False)
    cred = (await client.post(f"/api/v1/medical-tourism/providers/{p['id']}/credentials", json={"credential_type": "Licence"}, headers={"Authorization": f"Bearer {token}"})).json()["credential"]
    await client.post(f"/api/v1/medical-tourism/providers/{p['id']}/credentials/{cred['id']}/verify", headers={"Authorization": f"Bearer {token}"})
    work = await get_tool_registry().execute("medical_tourism.list_referral_work", {}, _ctx(tid))
    assert [r["clinic"] for r in work.ready_for_verification] == ["Almost Verified"]


async def test_a_reminder_goes_to_the_clinic_only_once_in_three_days_and_carries_no_patient_data(client, halla, monkeypatch) -> None:  # noqa: F811
    token, tid = await _gated(client, "Remind Co", "remindco@example.com")
    prov = await _provider(client, token, "Reminded Clinic")
    lead = await _patient(client, token)
    ref = await _approved(client, token, lead, prov["id"])
    await get_tool_registry().execute("medical_tourism.send_referral", {"referral_id": ref}, _ctx(tid), skip_approval_gate=True)
    reg = get_tool_registry()
    no_email = await reg.execute("medical_tourism.remind_clinic", {"referral_id": ref}, _ctx(tid), skip_approval_gate=True)
    assert no_email.reminded is False and "no contact e-mail" in no_email.reason
    await _set_provider(tid, prov["id"], contact_email="intake@clinic.example")
    s = get_settings()
    monkeypatch.setattr(s, "SENDGRID_API_KEY", None)
    assert "no real e-mail provider" in (await reg.execute("medical_tourism.remind_clinic", {"referral_id": ref}, _ctx(tid), skip_approval_gate=True)).reason
    fake = _FakeChannel()
    monkeypatch.setattr(s, "SENDGRID_API_KEY", "key-for-test")
    monkeypatch.setattr(s, "SENDGRID_FROM_EMAIL", "from@klaros.example")
    monkeypatch.setattr("app.communications.factory.get_clinic_channel", lambda session_factory: fake)
    first = await reg.execute("medical_tourism.remind_clinic", {"referral_id": ref}, _ctx(tid), skip_approval_gate=True)
    assert first.reminded is True and len(fake.sent) == 1 and fake.sent[0]["to"] == "intake@clinic.example"
    assert "Synthetic Patient" not in fake.sent[0]["body"] and "@example.com" not in fake.sent[0]["body"] and ref in fake.sent[0]["body"]  # a reference only, never the patient
    second = await reg.execute("medical_tourism.remind_clinic", {"referral_id": ref}, _ctx(tid), skip_approval_gate=True)
    assert second.reminded is False and "last 3 days" in second.reason and len(fake.sent) == 1


async def test_recording_an_approval_announces_it_so_the_crew_can_send_without_a_button(client, halla) -> None:  # noqa: F811
    from sqlalchemy import select

    from app.models.event import Event, EventType

    token, tid = await _gated(client, "Announce Co", "announce@example.com")
    provider = await _provider(client, token)
    ref = await _approved(client, token, await _patient(client, token), provider["id"])
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        events = list((await s.execute(select(Event).where(Event.tenant_id == tid, Event.event_type == EventType.MT_REFERRAL_APPROVED))).scalars())
    assert len(events) == 1 and str(events[0].entity_id) == ref
    assert set(events[0].payload) == {"referral_id", "provider_id"}  # ids only, never patient data
