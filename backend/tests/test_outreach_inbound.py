"""A clinic's emailed reply to an outreach email comes back through the existing inbound path and reaches the outreach reply handler, only for a LIVE campaign and only from the address we wrote to."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.session import async_session_maker
from app.models.clinic_outreach import ClinicOutreach, ClinicOutreachCampaign, ClinicOutreachMessage, OutreachMode, OutreachStage
from app.models.inbound_email import InboundEmail
from app.services import inbound_email_service as svc
from tests.test_business_journey_api import _register
from tests.test_clinic_outreach_engine import _campaign, _clinic_row, _set_cfg
from tests.test_halla_integration import _h, _tenant_id
from tests.test_inbound_email import DOMAIN, HOOK, _item, replies_on  # noqa: F401

pytestmark = pytest.mark.asyncio


async def _live_campaign(client, email, mode=OutreachMode.LIVE):
    token = await _register(client, f"Org {email}", email)
    tid = await _tenant_id(client, token)
    cid, ids = await _campaign(tid, ("Alpha Clinic",))
    await _set_cfg(cid, mode=mode)
    reply_to = await svc.reply_to_for(async_session_maker, tid, "info@alpha.example")
    assert reply_to and reply_to.endswith("@" + DOMAIN)  # the outreach contact address resolves to the clinic's hidden reply address
    return tid, cid, ids["Alpha Clinic"], reply_to.split("@")[0].removeprefix("r-")


async def _post(client, token, **kw):
    r = await client.post(HOOK, json={"items": [_item(token, **kw)]})
    assert r.status_code == 200, r.text
    return r


async def _msgs(oid):
    async with async_session_maker() as s:
        return list((await s.execute(select(ClinicOutreachMessage).where(ClinicOutreachMessage.outreach_id == oid))).scalars())


async def test_an_interested_reply_in_a_live_campaign_is_classified_and_answered_in_the_queue(client, replies_on) -> None:
    tid, cid, oid, token = await _live_campaign(client, "in1@example.com")
    await _post(client, token, sender="info@alpha.example", subject="Re: Introduction", text="We are interested, please tell us more.")
    assert (await _clinic_row(oid)).stage == OutreachStage.REPLIED
    kinds = {m.kind: m for m in await _msgs(oid)}
    assert kinds["reply_in"].status == "IN_INTERESTED" and kinds["reply_out"].status == "QUEUED_LIVE"
    async with async_session_maker() as s:
        stored = (await s.execute(select(InboundEmail).where(InboundEmail.tenant_id == tid))).scalars().all()
    assert len(stored) == 1 and stored[0].sender_matches is True  # the usual inbox record and notification still happen


async def test_an_opt_out_reply_suppresses_the_clinic_and_stops_follow_ups(client, replies_on) -> None:
    tid, cid, oid, token = await _live_campaign(client, "in2@example.com")
    await _post(client, token, sender="info@alpha.example", subject="Re: Introduction", text="Please unsubscribe us")
    assert (await _clinic_row(oid)).stage == OutreachStage.OPTED_OUT
    from app.services.clinic_outreach_service import ClinicOutreachService
    assert await ClinicOutreachService(async_session_maker).is_suppressed(tid, "info@alpha.example")


async def test_shadow_campaigns_and_look_alike_senders_are_not_handled(client, replies_on) -> None:
    tid, cid, oid, token = await _live_campaign(client, "in3@example.com", mode=OutreachMode.SHADOW)
    await _post(client, token, sender="info@alpha.example", text="We are interested")
    assert (await _clinic_row(oid)).stage == OutreachStage.READY and await _msgs(oid) == []  # a shadow campaign never acts on a real reply
    await _set_cfg(cid, mode=OutreachMode.LIVE)
    await _post(client, token, message_id="<m2@mail>", sender="someone-else@alpha.example", text="We are interested")
    assert (await _clinic_row(oid)).stage == OutreachStage.READY and await _msgs(oid) == []  # a different address than we wrote to
    await _post(client, token, message_id="<m3@mail>", sender="info@alpha.example", text="We are interested")
    assert (await _clinic_row(oid)).stage == OutreachStage.REPLIED


async def test_a_handling_failure_never_loses_the_stored_reply(client, replies_on, monkeypatch) -> None:
    tid, cid, oid, token = await _live_campaign(client, "in4@example.com")

    async def boom(*a, **k):
        raise RuntimeError("handler down")

    monkeypatch.setattr("app.services.clinic_outreach_service.ClinicOutreachService.handle_reply", boom)
    await _post(client, token, sender="info@alpha.example", text="We are interested")
    async with async_session_maker() as s:
        assert len((await s.execute(select(InboundEmail).where(InboundEmail.tenant_id == tid))).scalars().all()) == 1
    assert (await _clinic_row(oid)).stage == OutreachStage.READY
