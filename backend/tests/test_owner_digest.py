"""The owners get one morning e-mail of what needs them: only owners, once a day, never empty, and never a patient's name."""

from types import SimpleNamespace

import pytest

from app.db.session import async_session_maker
from app.services import operating_crew as oc
from app.services import owner_digest
from app.tools.policy import DEFAULT_TOOL_POLICIES, ActionPolicy
from tests.test_mt_referrals import _gated, _h, _provider, halla  # noqa: F401

pytestmark = pytest.mark.asyncio


class _Channel:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_email(self, tenant_id, **kw):
        self.sent.append(kw)
        return True


def _wire(monkeypatch, channel) -> None:
    import app.communications.email_config as cfg
    import app.communications.factory as factory

    monkeypatch.setattr(cfg, "email_configured", lambda: True)
    monkeypatch.setattr(factory, "get_internal_channel", lambda _sf: channel)


def test_the_digest_lists_each_item_with_its_priority_reason_and_link() -> None:
    item = SimpleNamespace(priority="HIGH", title="No clinic is verified yet", reason="Nothing can be referred.", link="/medical-tourism/agreements")
    subject, body = owner_digest.compose("MedLuxe Travel", [item], "https://meetklaros.com/")
    assert subject == "MedLuxe Travel: 1 thing needs you today"
    assert "[HIGH] No clinic is verified yet" in body and "https://meetklaros.com/medical-tourism/agreements" in body
    assert owner_digest.compose("X", [item, item], "https://a")[0] == "X: 2 things need you today"


async def test_it_sends_once_to_the_owner_and_not_again_the_same_day(client, halla, monkeypatch) -> None:  # noqa: F811
    token, tid = await _gated(client, "Digest Co", "digestco@example.com")
    channel = _Channel()
    _wire(monkeypatch, channel)
    await _provider(client, token, name="Unverified Clinic", verified=False)  # an unverified clinic is something that needs the owner
    first = await owner_digest.send_digest(async_session_maker, tid)
    assert first == {"sent": 1, "reason": None}
    assert len(channel.sent) == 1 and channel.sent[0]["to"] == "digestco@example.com" and "No clinic is verified yet" in channel.sent[0]["body"]
    again = await owner_digest.send_digest(async_session_maker, tid)
    assert again == {"sent": 0, "reason": "already_sent_today"} and len(channel.sent) == 1


async def test_nothing_is_sent_when_nothing_needs_the_owner(client, halla, monkeypatch) -> None:  # noqa: F811
    token, tid = await _gated(client, "Quiet Co", "quietco@example.com")
    channel = _Channel()
    _wire(monkeypatch, channel)
    assert await owner_digest.send_digest(async_session_maker, tid) == {"sent": 0, "reason": "nothing_needs_you"} and channel.sent == []


async def test_the_crew_member_runs_the_tool_directly_and_the_tool_is_automatic() -> None:
    member = next(m for m in oc.MEDICAL_TOURISM_CREW if m.key == "owner-digest")
    assert oc._triggers(member)["schedule"]["action"] == {"tool_name": "notifications.send_owner_digest", "tool_input": {}}
    assert member.schedule_time == "08:15" and DEFAULT_TOOL_POLICIES["notifications.send_owner_digest"] == ActionPolicy.AUTO
