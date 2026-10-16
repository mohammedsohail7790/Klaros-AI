"""The patient is told, once each, when their enquiry is shared with a clinic and when the clinic accepts it: consent-guarded, no medical detail, never blocking the referral."""

from types import SimpleNamespace

import pytest

from app.services import mt_patient_updates
from tests.test_mt_referrals import BASE, _approved, _gated, _h, _patient, _provider, _send, halla  # noqa: F401

pytestmark = pytest.mark.asyncio


class _Comms:
    def __init__(self, *, blocked: bool = False) -> None:
        self.sent, self.blocked = [], blocked

    async def deliver_email(self, tenant_id, **kw):
        self.sent.append(kw)
        return SimpleNamespace(blocked=self.blocked, sent=not self.blocked)


def _wire(monkeypatch, comms) -> None:
    import app.communications.email_config as cfg
    import app.communications.factory as factory

    monkeypatch.setattr(cfg, "email_configured", lambda: True)
    monkeypatch.setattr(factory, "get_communication_provider", lambda _sf: comms)


def test_the_messages_name_the_clinic_and_carry_no_medical_detail() -> None:
    subject, body = mt_patient_updates.compose("referral_sent", first_name="Sam", clinic="Apex Hospital", business="MedLuxe Travel")
    assert "Apex Hospital" in subject and "Hello Sam" in body and "MedLuxe Travel" in body
    subject2, body2 = mt_patient_updates.compose("clinic_accepted", first_name="", clinic="Apex Hospital", business="MedLuxe Travel")
    assert "accepted" in subject2 and body2.startswith("Hello there")
    for text in (subject, body, subject2, body2):
        assert "$" not in text and "treatment" not in text.lower() and "dental" not in text.lower()


async def test_sending_a_referral_and_the_clinic_accepting_each_email_the_patient_once(client, halla, monkeypatch) -> None:  # noqa: F811
    token, _tid = await _gated(client, "Updates Co", "updates@example.com")
    comms = _Comms()
    _wire(monkeypatch, comms)
    provider = await _provider(client, token, name="Apex Hospital")
    lead = await _patient(client, token)
    ref = await _approved(client, token, lead, provider["id"])
    assert (await _send(client, token, ref)).status_code == 200
    assert [m["subject"] for m in comms.sent] == ["Your enquiry has been passed to Apex Hospital"]
    await _send(client, token, ref)  # a repeated send is not a second email
    assert len(comms.sent) == 1
    for _ in range(2):
        await client.post(f"{BASE}/referrals/{ref}/clinic-response", json={"accepted": True}, headers=_h(token))
    assert [m["subject"] for m in comms.sent] == ["Your enquiry has been passed to Apex Hospital", "Apex Hospital has accepted your enquiry"]


async def test_a_declined_referral_sends_nothing_and_no_consent_means_no_email(client, halla, monkeypatch) -> None:  # noqa: F811
    token, _tid = await _gated(client, "Updates Blocked Co", "updatesblocked@example.com")
    comms = _Comms(blocked=True)
    _wire(monkeypatch, comms)
    provider = await _provider(client, token, name="Beta Hospital")
    ref = await _approved(client, token, await _patient(client, token), provider["id"])
    assert (await _send(client, token, ref)).status_code == 200  # the referral itself is not affected by the refused update
    await client.post(f"{BASE}/referrals/{ref}/clinic-response", json={"accepted": False}, headers=_h(token))
    assert len(comms.sent) == 1  # only the (refused) send-stage attempt; a decline never emails the patient
