"""A website enquiry now creates work a person can see (it used to take a phone call), qualification asks for clinic options, and sending a referral closes that work."""

import pytest
from sqlalchemy import select

from app.events.mt_task_handlers import register_mt_task_handlers
from app.db.session import async_session_maker, set_tenant_context
from app.models.event import EventType
from app.models.medical_tourism_referral import HandoffTask
from tests.test_consent_gate_intake import _enable_vertical
from tests.test_mt_referrals import BASE, _approved, _gated, _h, _patient, _provider, _send, halla  # noqa: F401
from tests.test_halla_integration import _lead, _register, _tenant_id

pytestmark = pytest.mark.asyncio


async def _tasks(tid):
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        return list((await s.execute(select(HandoffTask).where(HandoffTask.tenant_id == tid))).scalars())


async def test_a_new_medical_tourism_enquiry_creates_one_task_and_a_referral_closes_it(client, event_bus, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Case Tasks Co", "casetasks@example.com")
    register_mt_task_handlers(event_bus, async_session_maker)
    await _enable_vertical(tid)
    await event_bus.process_pending(EventType.LEAD_CREATED)  # create the consumer groups first, as the running worker does at start-up
    provider = await _provider(client, token)
    lead = await _patient(client, token)
    await event_bus.process_pending(EventType.LEAD_CREATED)
    kinds = {t.kind: t for t in await _tasks(tid) if str(t.lead_id) == lead}
    assert "new_enquiry" in kinds and kinds["new_enquiry"].status == "OPEN"
    listed = (await client.get(f"{BASE}/tasks", headers=_h(token))).json()
    assert any(t["kind"] == "new_enquiry" for t in listed["tasks"])
    ref = await _approved(client, token, lead, provider["id"])
    await _send(client, token, ref)
    after = [t for t in await _tasks(tid) if str(t.lead_id) == lead]
    assert after and all(t.status == "DONE" for t in after if t.kind in ("new_enquiry", "provider_options"))
    assert len([t for t in after if t.kind == "new_enquiry"]) == 1  # never doubled


async def test_an_enquiry_for_a_business_without_the_module_creates_no_medical_task(client, event_bus) -> None:
    token = await _register(client, "Plain Co", "plainco@example.com")
    tid = await _tenant_id(client, token)
    register_mt_task_handlers(event_bus, async_session_maker)
    await event_bus.process_pending(EventType.LEAD_CREATED)
    await _lead(client, token, phone="+971500000081")
    await event_bus.process_pending(EventType.LEAD_CREATED)
    assert await _tasks(tid) == []
