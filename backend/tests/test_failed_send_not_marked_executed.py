"""A provider that cannot deliver must leave the activity FAILED, never EXECUTED
(an undelivered nurture / outbound / retention message must not look sent)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.communications.base import CommunicationProvider
from app.db.session import async_session_maker, set_tenant_context
from app.events.bus import EventBus
from app.events.transport import InMemoryTransport
from app.models.crm import Customer, Lead
from app.models.marketing import (
    ActivityStatus,
    NurtureActivity,
    NurtureEnrollment,
    OutboundActivity,
    OutboundContact,
    OutboundEnrollment,
    OutboundStep,
)
from app.models.retention import RetentionActivity, RetentionActivityStatus, RetentionEnrollment
from app.services.nurture_service import NurtureService
from app.services.outbound_service import OutboundService
from app.services.retention_campaign_service import RetentionCampaignService

pytestmark = pytest.mark.asyncio


class DownProvider(CommunicationProvider):
    """Every send fails, as when the email service rejects or is unreachable."""

    provider_name = "down"

    async def send_email(self, tenant_id, *, to, subject, body, template) -> bool:
        return False

    async def send_sms(self, tenant_id, *, to, body, template) -> bool:
        return False


async def test_failed_send_is_recorded_failed_not_executed(tool_registry) -> None:
    tid = uuid.uuid4()
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    comms = DownProvider()
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        lead = Lead(tenant_id=tid, name="Pat", email="pat@example.com", source="WEB")
        cust = Customer(tenant_id=tid, name="Cy", email="cy@example.com")
        s.add_all([lead, cust])
        await s.flush()
        ne = NurtureEnrollment(tenant_id=tid, sequence_id=uuid.uuid4(), lead_id=lead.id, enrolled_at=past)
        re_ = RetentionEnrollment(tenant_id=tid, campaign_id=uuid.uuid4(), customer_id=cust.id, enrolled_at=past)
        oc = OutboundContact(tenant_id=tid, list_id=uuid.uuid4(), email="cold@example.com")
        seq = uuid.uuid4()
        step = OutboundStep(tenant_id=tid, sequence_id=seq, day_offset=0, subject="Hi", body="Body")
        s.add_all([ne, re_, oc, step])
        await s.flush()
        oe = OutboundEnrollment(tenant_id=tid, sequence_id=seq, contact_id=oc.id, enrolled_at=past)
        s.add(oe)
        await s.flush()
        na = NurtureActivity(tenant_id=tid, enrollment_id=ne.id, scheduled_for=past)
        ra = RetentionActivity(tenant_id=tid, enrollment_id=re_.id, scheduled_for=past, template="WIN_BACK")
        oa = OutboundActivity(tenant_id=tid, enrollment_id=oe.id, step_id=step.id, scheduled_for=past)
        s.add_all([na, ra, oa])
        await s.commit()
        ids = (na.id, ra.id, oa.id)

    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    assert await NurtureService(async_session_maker, bus, comms).execute_due_activities(tid) == []
    assert await RetentionCampaignService(async_session_maker, comms).execute_due_activities(tid) == []
    assert await OutboundService(async_session_maker, comms).execute_due_activities(tid) == []

    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        assert (await s.get(NurtureActivity, ids[0])).status == ActivityStatus.FAILED
        assert (await s.get(RetentionActivity, ids[1])).status == RetentionActivityStatus.FAILED
        assert (await s.get(OutboundActivity, ids[2])).status == ActivityStatus.FAILED
