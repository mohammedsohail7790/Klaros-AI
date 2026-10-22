"""Sends the clinic outreach emails the engine has queued in LIVE campaigns, one at a time, behind every guard that matters.

Nothing is sent unless the campaign is ACTIVE and LIVE, the AI kill switch is off, a real email provider is configured, and the message's time has come. Right before each
send it re-checks what could have changed since the draft was made: suppression, whether the clinic has replied or been handed to a person, the Clinic Onboarder's 14-day
rule, and the daily cap counted by emails actually sent today. A failed send is retried once the next day; a second failure hands the clinic to a person. With at least ten
emails sent, a bounce or opt-out rate above five percent pauses the campaign. Every query is scoped to the tenant; audit rows hold ids and counts only.
"""

from __future__ import annotations

import asyncio
import uuid
import weakref
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.communications.base import MessageTemplate
from app.communications.email_config import email_configured
from app.communications.factory import get_clinic_channel
from app.db.session import set_tenant_context
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.clinic_outreach import ClinicOutreach, ClinicOutreachCampaign, ClinicOutreachMessage, OutreachMessageStatus, OutreachMode, OutreachStage
from app.models.marketing import Campaign, CampaignStatus
from app.models.medical_tourism import Provider
from app.services.clinic_outreach_engine import ONBOARDER_QUIET, ClinicOutreachEngine, _utc
from app.services.clinic_outreach_service import ClinicOutreachService
from app.services.outreach_schedule import warmup_cap
from app.services.outreach_settings import OutreachConfig
from app.tools.builtin.mt_referral_tools import INVITE_ACTION

logger = structlog.get_logger(__name__)

MIN_SENT_FOR_RATES = 10
MAX_BAD_RATE = 0.05
MAX_ATTEMPTS = 2
SEND_ACTION = "outreach.message_sent"
AUTO_PAUSE_ACTION = "outreach.auto_paused"
FOLLOW_UPS = ("intro", "followup_1", "followup_2")
_LIVE_STAGES = (OutreachStage.INTRO_SENT, OutreachStage.FOLLOWUP_1, OutreachStage.FOLLOWUP_2)
_CONFIRMATION_STAGES = (OutreachStage.OPTED_OUT, OutreachStage.DECLINED)  # the one closing message to a clinic that asked us to stop is allowed

_locks: weakref.WeakValueDictionary[uuid.UUID, asyncio.Lock] = weakref.WeakValueDictionary()


def _lock_for(tenant_id: uuid.UUID) -> asyncio.Lock:
    lock = _locks.get(tenant_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[tenant_id] = lock
    return lock


class ClinicOutreachSender:
    def __init__(self, session_factory, channel_factory=None) -> None:
        self._sf = session_factory
        self._channel = channel_factory or (lambda: get_clinic_channel(session_factory))

    async def send_due(self, tenant_id: uuid.UUID, *, now: datetime | None = None) -> dict[str, int]:
        now = (now or datetime.now(UTC)).astimezone(UTC)
        totals = {"campaigns": 0, "sent": 0, "failed": 0, "cancelled": 0, "deferred": 0, "held": 0, "paused": 0}
        async with _lock_for(tenant_id):
            async with self._sf() as session:
                await set_tenant_context(session, tenant_id)
                campaign_ids = list((await session.execute(
                    select(Campaign.id).join(ClinicOutreachCampaign, ClinicOutreachCampaign.campaign_id == Campaign.id)
                    .where(Campaign.tenant_id == tenant_id, ClinicOutreachCampaign.tenant_id == tenant_id, Campaign.status == CampaignStatus.ACTIVE,
                           ClinicOutreachCampaign.mode == OutreachMode.LIVE).order_by(Campaign.created_at, Campaign.id))).scalars())
            if campaign_ids and not email_configured():
                totals["held"] += len(campaign_ids)  # nothing real could be delivered, so nothing is attempted
                return totals
            for campaign_id in campaign_ids:
                totals["campaigns"] += 1
                counts = await self._run_campaign(tenant_id, campaign_id, now)
                for key, value in counts.items():
                    totals[key] += value
        return totals

    async def _run_campaign(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID, now: datetime) -> dict[str, int]:
        counts = {"sent": 0, "failed": 0, "cancelled": 0, "deferred": 0, "held": 0, "paused": 0}
        async with self._sf() as session:
            await set_tenant_context(session, tenant_id)
            state = await ClinicOutreachEngine._state(session, tenant_id, campaign_id)
            cfg = (await session.execute(select(ClinicOutreachCampaign).where(ClinicOutreachCampaign.campaign_id == campaign_id, ClinicOutreachCampaign.tenant_id == tenant_id))).scalar_one_or_none()
            if state is None or state[0] != CampaignStatus.ACTIVE or state[1] or cfg is None or cfg.mode != OutreachMode.LIVE:
                return counts
            try:
                limits = OutreachConfig.model_validate({"term_sheet": cfg.term_sheet, "sender": cfg.sender, "limits": cfg.limits or {}, "countries": cfg.countries}).limits
            except ValidationError:
                counts["held"] += 1
                return counts
            live_at = _utc(cfg.live_at) if cfg.live_at is not None else None
            join = ClinicOutreach.id == ClinicOutreachMessage.outreach_id
            scope = (ClinicOutreachMessage.tenant_id == tenant_id, ClinicOutreach.tenant_id == tenant_id, ClinicOutreach.campaign_id == campaign_id)
            sent = (await session.execute(select(func.count(ClinicOutreachMessage.id)).join(ClinicOutreach, join).where(*scope, ClinicOutreachMessage.status == OutreachMessageStatus.SENT))).scalar_one()
            bounced = (await session.execute(select(func.count(ClinicOutreachMessage.id)).join(ClinicOutreach, join).where(*scope, ClinicOutreachMessage.status == "IN_BOUNCE"))).scalar_one()
            opted_out = (await session.execute(select(func.count(ClinicOutreachMessage.id)).join(ClinicOutreach, join).where(*scope, ClinicOutreachMessage.status == "IN_OPT_OUT"))).scalar_one()
            day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            sent_today = (await session.execute(select(func.count(ClinicOutreachMessage.id)).join(ClinicOutreach, join).where(
                *scope, ClinicOutreachMessage.status == OutreachMessageStatus.SENT, ClinicOutreachMessage.kind.in_(FOLLOW_UPS), ClinicOutreachMessage.sent_at >= day_start))).scalar_one()
            due = list((await session.execute(
                select(ClinicOutreachMessage.id, ClinicOutreachMessage.kind).join(ClinicOutreach, join)
                .where(*scope, ClinicOutreachMessage.direction == "OUT", ClinicOutreachMessage.status == OutreachMessageStatus.QUEUED_LIVE, ClinicOutreachMessage.scheduled_for <= now)
                .order_by(ClinicOutreachMessage.scheduled_for, ClinicOutreachMessage.id))).all())

        if sent >= MIN_SENT_FOR_RATES and (bounced / sent > MAX_BAD_RATE or opted_out / sent > MAX_BAD_RATE):
            await self._auto_pause(tenant_id, campaign_id, round(bounced / sent, 3), round(opted_out / sent, 3))
            counts["paused"] += 1
            return counts

        remaining = warmup_cap(limits, live_at, now) - int(sent_today)
        # Replies to a clinic (answers, opt-out confirmations) come first and are never held back by the daily cap, which counts introductions and follow-ups only.
        due.sort(key=lambda r: 0 if r[1] == "reply_out" else 1)
        for message_id, kind in due:
            capped = kind in FOLLOW_UPS
            if capped and remaining <= 0:
                counts["deferred"] += 1
                continue
            try:
                outcome = await self._send_one(tenant_id, campaign_id, message_id, now)
            except Exception as exc:  # noqa: BLE001 - one message must never stop the others
                logger.warning("outreach_send_failed", message_id=str(message_id), error_type=type(exc).__name__)
                counts["held"] += 1
                continue
            if outcome == "stop":
                break
            if outcome == "sent" and capped:
                remaining -= 1
            if outcome in counts:
                counts[outcome] += 1
        return counts

    async def _auto_pause(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID, bounce_rate: float, opt_out_rate: float) -> None:
        async with self._sf() as session:
            await set_tenant_context(session, tenant_id)
            campaign = (await session.execute(select(Campaign).where(Campaign.id == campaign_id, Campaign.tenant_id == tenant_id))).scalar_one_or_none()
            if campaign is None or campaign.status != CampaignStatus.ACTIVE:
                return
            campaign.status = CampaignStatus.PAUSED
            session.add(ClinicOutreachService._audit(tenant_id, None, AUTO_PAUSE_ACTION, campaign_id, {"campaign_id": str(campaign_id), "bounce_rate": bounce_rate, "opt_out_rate": opt_out_rate},
                                                     actor_type=ActorType.SYSTEM))
            ClinicOutreachService._notify(session, tenant_id, "Clinic outreach paused itself",
                                          "Too many emails bounced or clinics asked us to stop, so the campaign was paused to protect your sender reputation. Review it before resuming.", "WARNING", campaign_id)
            await session.commit()

    async def _send_one(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID, message_id: uuid.UUID, now: datetime) -> str:
        """sent | failed | cancelled | deferred | held | done | stop. Everything is decided and written inside one transaction that holds the message row."""
        async with self._sf() as session:
            await set_tenant_context(session, tenant_id)
            m = (await session.execute(select(ClinicOutreachMessage).where(ClinicOutreachMessage.id == message_id, ClinicOutreachMessage.tenant_id == tenant_id).with_for_update())).scalar_one_or_none()
            if m is None or m.status != OutreachMessageStatus.QUEUED_LIVE:
                return "done"
            cfg = (await session.execute(select(ClinicOutreachCampaign).where(ClinicOutreachCampaign.campaign_id == campaign_id, ClinicOutreachCampaign.tenant_id == tenant_id))).scalar_one_or_none()
            state = await ClinicOutreachEngine._state(session, tenant_id, campaign_id)
            if cfg is None or state is None or state[0] != CampaignStatus.ACTIVE or state[1] or cfg.mode != OutreachMode.LIVE:
                return "stop"
            clinic = (await session.execute(select(ClinicOutreach).where(ClinicOutreach.id == m.outreach_id, ClinicOutreach.tenant_id == tenant_id, ClinicOutreach.campaign_id == campaign_id).with_for_update())).scalar_one_or_none()
            if clinic is None:
                return "done"

            def cancel(reason: str, *, park: bool = False) -> str:
                m.status, m.failure_reason = OutreachMessageStatus.CANCELLED, reason[:255]
                if park and clinic.stage not in _CONFIRMATION_STAGES:
                    clinic.stage, clinic.handoff_reason, clinic.next_action_at = OutreachStage.NEEDS_PERSON, reason, None
                return "cancelled"

            address = (clinic.contact_email or "").strip()
            if not address:
                result = cancel("the clinic has no email address on file", park=True)
            elif m.kind in FOLLOW_UPS and clinic.stage not in _LIVE_STAGES:
                result = cancel("the clinic has replied or been handed to a person")
            elif m.kind == "reply_out" and clinic.stage == OutreachStage.NEEDS_PERSON:
                result = cancel("a person has taken over this clinic")
            elif await ClinicOutreachService.is_suppressed_in(session, tenant_id, address) and not (m.kind == "reply_out" and clinic.stage in _CONFIRMATION_STAGES):
                result = cancel("the address is on the suppression list", park=True)
            else:
                invited = (await session.execute(select(func.max(AuditLog.created_at)).where(
                    AuditLog.tenant_id == tenant_id, AuditLog.action == INVITE_ACTION, AuditLog.entity_id == clinic.provider_id, AuditLog.created_at >= now - ONBOARDER_QUIET))).scalar_one()
                if invited is not None and m.kind in FOLLOW_UPS:
                    m.scheduled_for = _utc(invited) + ONBOARDER_QUIET  # the Onboarder already wrote to this clinic: wait out its quiet period
                    m.schedule_day = m.scheduled_for.date()
                    try:
                        await session.commit()
                    except IntegrityError:
                        await session.rollback()
                    return "deferred"
                result = None
            if result is not None:
                await session.commit()
                return result

            provider = (await session.execute(select(Provider).where(Provider.id == clinic.provider_id, Provider.tenant_id == tenant_id))).scalar_one_or_none()
            ok = False
            try:
                ok = bool(await self._channel().send_email(tenant_id, to=address, subject=m.subject, body=m.body, template=MessageTemplate.REFERRAL_INVITATION))
            except Exception as exc:  # noqa: BLE001 - a provider error is a failed attempt, never a crash
                logger.warning("outreach_send_error", message_id=str(message_id), error_type=type(exc).__name__)
            m.attempts = (m.attempts or 0) + 1
            if ok:
                m.status, m.sent_at, m.failure_reason = OutreachMessageStatus.SENT, now, None
                clinic.emails_sent, clinic.last_sent_at = (clinic.emails_sent or 0) + 1, now
                session.add(AuditLog(tenant_id=tenant_id, actor_type=ActorType.SYSTEM, actor_id=None, action=SEND_ACTION, entity_type="clinic_outreach", entity_id=clinic.id,
                                     input_summary={"campaign_id": str(campaign_id), "message_id": str(m.id), "kind": m.kind}, result="success"))
                await session.commit()
                return "sent"
            m.failure_reason = "the email provider did not deliver the message"
            if m.attempts < MAX_ATTEMPTS:
                m.scheduled_for = now + timedelta(days=1)  # one retry, tomorrow
                m.schedule_day = m.scheduled_for.date()
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                return "failed"
            m.status = OutreachMessageStatus.FAILED
            if clinic.stage not in _CONFIRMATION_STAGES:
                clinic.stage, clinic.handoff_reason, clinic.next_action_at = OutreachStage.NEEDS_PERSON, "the email could not be delivered after two tries", None
            ClinicOutreachService._notify(session, tenant_id, "An outreach email could not be delivered",
                                          f"{provider.name if provider else 'A clinic'} could not be emailed after two tries. A person needs to look at it.", "WARNING", clinic.id)
            await session.commit()
            return "failed"
