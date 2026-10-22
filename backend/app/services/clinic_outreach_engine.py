"""Clinic outreach shadow engine: drafts the next email for each due clinic and stores it. NOTHING here sends anything.

The engine only writes `ClinicOutreachMessage` rows (status QUEUED_SHADOW or BLOCKED) and moves clinics along their pipeline stages as a live run
would. It never touches a message channel, `emails_sent` or `last_sent_at`. Every query is scoped to the tenant.

Concurrency: one in-process asyncio.Lock per tenant and per campaign (held in weak dicts, so they vanish when idle), and, inside every clinic's own
transaction, a row lock on the campaign (SELECT ... FOR UPDATE on Postgres; ignored by SQLite). A clinic that already has a draft of the kind it
is about to get is never drafted again, so overlapping or repeated runs make exactly one draft per clinic and kind.
"""

from __future__ import annotations

import asyncio
import uuid
import weakref
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog
from pydantic import ValidationError
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError

from app.db.session import set_tenant_context
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.clinic_outreach import (ClinicOutreach, ClinicOutreachCampaign, ClinicOutreachMessage, OutreachMessageKind, OutreachMessageStatus,
                                        OutreachMode, OutreachStage)
from app.models.marketing import Campaign, CampaignStatus
from app.models.medical_tourism import Provider
from app.models.organization import Organization
from app.services.clinic_outreach_service import ClinicOutreachService
from app.services.outreach_composer import ClinicRef, check, compose
from app.services.outreach_schedule import FOLLOWUP_GAPS, NO_RESPONSE_AFTER, next_send_slot, timezone_for, warmup_cap
from app.services.outreach_settings import OutreachConfig
from app.tools.builtin.mt_referral_tools import INVITE_ACTION

logger = structlog.get_logger(__name__)

HORIZON = timedelta(hours=24)
FIRST_WAVE_DAYS = timedelta(days=7)
FIRST_WAVE_CLINICS = 10
ONBOARDER_QUIET = timedelta(days=14)
FAILURES_BEFORE_PARKING = 3
ERROR_ACTION = "outreach.engine_clinic_error"
RECOVERED_ACTION = "outreach.engine_clinic_recovered"
RUN_ACTION = "outreach.engine_run"

# current stage -> (kind of draft to make, stage after the draft, gap from the slot to the next due time)
_STEPS: dict[str, tuple[OutreachMessageKind, OutreachStage, timedelta]] = {
    OutreachStage.READY: (OutreachMessageKind.INTRO, OutreachStage.INTRO_SENT, FOLLOWUP_GAPS[OutreachMessageKind.FOLLOWUP_1]),
    OutreachStage.INTRO_SENT: (OutreachMessageKind.FOLLOWUP_1, OutreachStage.FOLLOWUP_1, FOLLOWUP_GAPS[OutreachMessageKind.FOLLOWUP_2]),
    OutreachStage.FOLLOWUP_1: (OutreachMessageKind.FOLLOWUP_2, OutreachStage.FOLLOWUP_2, NO_RESPONSE_AFTER),
}
_DRAFTING_STAGES = tuple(s.value for s in _STEPS)

_NOT_COUNTED = (OutreachMessageStatus.BLOCKED, OutreachMessageStatus.SHADOW_ARCHIVED, OutreachMessageStatus.CANCELLED)  # never counted as a draft of its kind or against the cap

SUPPRESSED = "address is on the suppression list"
NO_TIME_ZONE = "clinic country has no known time zone"
NO_ADDRESS = "clinic has no email address on file"
COULD_NOT_PROCESS = "the outreach engine could not process this clinic"

# One lock per tenant and per campaign. Weak values: a lock disappears once nothing holds it, so the dicts stay bounded.
_tenant_locks: weakref.WeakValueDictionary[uuid.UUID, asyncio.Lock] = weakref.WeakValueDictionary()
_campaign_locks: weakref.WeakValueDictionary[uuid.UUID, asyncio.Lock] = weakref.WeakValueDictionary()


def _lock_for(table: weakref.WeakValueDictionary, key: uuid.UUID) -> asyncio.Lock:
    lock = table.get(key)
    if lock is None:
        lock = asyncio.Lock()
        table[key] = lock
    return lock


def _utc(value: datetime) -> datetime:
    """SQLite hands datetimes back naive; they were stored as UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _schedule_day(scheduled_for: datetime) -> date:
    """The UTC date of a slot. The only place `ClinicOutreachMessage.schedule_day` is computed."""
    return _utc(scheduled_for).date()


@dataclass
class _Outcome:
    kind: str  # composed | blocked | parked | skipped | day_full | stop_state | done


class ClinicOutreachEngine:
    def __init__(self, session_factory) -> None:
        self._sf = session_factory

    async def _between_clinics(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID, outreach_id: uuid.UUID) -> None:
        """Called before each clinic is processed. Does nothing; exists so a test can change the world mid-run."""

    async def run(self, tenant_id: uuid.UUID, *, now: datetime | None = None) -> dict[str, int]:
        now = now or datetime.now(UTC)
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        now = now.astimezone(UTC)
        totals = {"campaigns": 0, "composed": 0, "blocked": 0, "skipped": 0, "parked": 0}
        async with _lock_for(_tenant_locks, tenant_id):
            async with self._sf() as session:
                await set_tenant_context(session, tenant_id)
                campaign_ids = list((await session.execute(
                    select(Campaign.id).join(ClinicOutreachCampaign, ClinicOutreachCampaign.campaign_id == Campaign.id)
                    .where(Campaign.tenant_id == tenant_id, ClinicOutreachCampaign.tenant_id == tenant_id, Campaign.status == CampaignStatus.ACTIVE)
                    .order_by(Campaign.created_at, Campaign.id))).scalars())
            for campaign_id in campaign_ids:
                totals["campaigns"] += 1
                async with _lock_for(_campaign_locks, campaign_id):
                    counts = await self._run_campaign(tenant_id, campaign_id, now)
                for key, value in counts.items():
                    totals[key] += value
        return totals

    async def _run_campaign(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID, now: datetime) -> dict[str, int]:
        counts = {"composed": 0, "blocked": 0, "skipped": 0, "parked": 0}
        service = ClinicOutreachService(self._sf)
        async with self._sf() as session:
            await set_tenant_context(session, tenant_id)
            state = await self._state(session, tenant_id, campaign_id)
            if state is None or state[0] != CampaignStatus.ACTIVE:
                return counts  # stopped between the listing and now
            cfg_row = (await session.execute(select(ClinicOutreachCampaign).where(
                ClinicOutreachCampaign.campaign_id == campaign_id, ClinicOutreachCampaign.tenant_id == tenant_id))).scalar_one_or_none()
            if cfg_row is None:
                return counts
            mode, paused = cfg_row.mode, state[1]
            try:
                config = OutreachConfig.model_validate({"term_sheet": cfg_row.term_sheet, "sender": cfg_row.sender, "limits": cfg_row.limits or {},
                                                        "countries": cfg_row.countries})
            except ValidationError:
                config = None
            started = cfg_row.live_at if mode == OutreachMode.LIVE else cfg_row.shadow_started_at  # the ramp counts from whichever phase is running
            shadow_started_at = _utc(started) if started is not None else None
        try:
            view = await service.get_blocks(tenant_id)  # ONE read: the approval check and the wording used come from the same snapshot
        except Exception:  # noqa: BLE001 - unreadable wording is treated as not approved
            view = None
        if paused or mode not in (OutreachMode.SHADOW, OutreachMode.LIVE) or config is None or view is None or view["status"] != "APPROVED":
            counts["skipped"] += 1  # kill switch on, unusable settings, or wording not approved
            return counts
        blocks, version = view["blocks"], view["version"]

        horizon_end = now + HORIZON
        due = ClinicOutreach.next_action_at <= horizon_end
        async with self._sf() as session:
            await set_tenant_context(session, tenant_id)
            candidates = list((await session.execute(
                select(ClinicOutreach.id)
                .where(ClinicOutreach.tenant_id == tenant_id, ClinicOutreach.campaign_id == campaign_id,
                       ((ClinicOutreach.stage == OutreachStage.READY) & (ClinicOutreach.next_action_at.is_(None) | due))
                       | (ClinicOutreach.stage.in_([OutreachStage.INTRO_SENT.value, OutreachStage.FOLLOWUP_1.value]) & due)
                       | ((ClinicOutreach.stage == OutreachStage.FOLLOWUP_2) & (ClinicOutreach.next_action_at <= now)))
                # due transitions (FOLLOWUP_2 -> NO_RESPONSE, no draft, never capped) first, then follow-ups, then intros, each longest-overdue first
                # (a never-touched READY clinic counts as due now), so an intro backlog cannot starve a follow-up due later in the horizon
                .order_by(case((ClinicOutreach.stage == OutreachStage.FOLLOWUP_2, 0),
                          (ClinicOutreach.stage.in_([OutreachStage.INTRO_SENT.value, OutreachStage.FOLLOWUP_1.value]), 1), else_=2), func.coalesce(ClinicOutreach.next_action_at, now),
                          ClinicOutreach.id))).scalars())

        for index, outreach_id in enumerate(candidates):
            await self._between_clinics(tenant_id, campaign_id, outreach_id)
            try:
                outcome = await self._process(tenant_id, campaign_id, outreach_id, config, blocks, version, now, shadow_started_at)
            except Exception as exc:  # noqa: BLE001 - one clinic must never stop the others
                logger.warning("outreach_engine_clinic_failed", outreach_id=str(outreach_id), error_type=type(exc).__name__)
                counts["parked" if await self._record_failure(tenant_id, campaign_id, outreach_id) else "skipped"] += 1  # each clinic counted once
                continue
            if outcome.kind == "stop_state":
                counts["skipped"] += len(candidates) - index  # this clinic and every one after it
                break
            if outcome.kind != "day_full":  # a clinic that was never really attempted (its day is full) neither clears nor extends a failure streak
                await self._clear_failures(tenant_id, campaign_id, outreach_id)  # any other run that did not raise ends the streak
            if outcome.kind == "day_full":
                counts["skipped"] += 1
            elif outcome.kind in counts:
                counts[outcome.kind] += 1

        async with self._sf() as session:
            await set_tenant_context(session, tenant_id)
            row = (await session.execute(select(ClinicOutreachCampaign).where(
                ClinicOutreachCampaign.campaign_id == campaign_id, ClinicOutreachCampaign.tenant_id == tenant_id))).scalar_one()
            row.engine_last_run_at = now
            session.add(ClinicOutreachService._audit(tenant_id, None, RUN_ACTION, campaign_id, dict(counts), actor_type=ActorType.SYSTEM))
            await session.commit()
        return counts

    @staticmethod
    async def _state(session, tenant_id: uuid.UUID, campaign_id: uuid.UUID) -> tuple[str, bool] | None:
        """(campaign status, kill switch on), freshly read."""
        status = (await session.execute(select(Campaign.status).where(Campaign.id == campaign_id, Campaign.tenant_id == tenant_id))).scalar_one_or_none()
        if status is None:
            return None
        org = await session.get(Organization, tenant_id)  # the same lookup the tool registry uses for the AI kill switch
        return status, bool(org is not None and org.ai_paused)

    async def _process(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID, outreach_id: uuid.UUID, config: OutreachConfig, blocks: dict[str, str],
                       version: int, now: datetime, shadow_started_at: datetime | None) -> _Outcome:
        horizon_end = now + HORIZON
        async with self._sf() as session:
            await set_tenant_context(session, tenant_id)
            # Lock the campaign for this clinic, then re-read what could have changed since the run began.
            cfg_row = (await session.execute(select(ClinicOutreachCampaign).where(
                ClinicOutreachCampaign.campaign_id == campaign_id, ClinicOutreachCampaign.tenant_id == tenant_id).with_for_update())).scalar_one_or_none()
            state = await self._state(session, tenant_id, campaign_id)
            if cfg_row is None or state is None or state[0] != CampaignStatus.ACTIVE or state[1] or cfg_row.mode not in (OutreachMode.SHADOW, OutreachMode.LIVE):
                return _Outcome("stop_state")
            clinic = (await session.execute(select(ClinicOutreach).where(
                ClinicOutreach.id == outreach_id, ClinicOutreach.tenant_id == tenant_id, ClinicOutreach.campaign_id == campaign_id).with_for_update())).scalar_one_or_none()
            if clinic is None or clinic.stage not in (*_DRAFTING_STAGES, OutreachStage.FOLLOWUP_2.value):
                return _Outcome("done")  # moved on (replied, parked, already processed) since the run began

            if clinic.stage == OutreachStage.FOLLOWUP_2:
                if clinic.next_action_at is not None and _utc(clinic.next_action_at) <= now:
                    clinic.stage, clinic.next_action_at = OutreachStage.NO_RESPONSE, None
                    await session.commit()
                return _Outcome("done")

            kind, next_stage, gap = _STEPS[clinic.stage]
            existing = (await session.execute(select(ClinicOutreachMessage.scheduled_for).where(
                ClinicOutreachMessage.tenant_id == tenant_id, ClinicOutreachMessage.outreach_id == outreach_id, ClinicOutreachMessage.kind == kind.value,
                ClinicOutreachMessage.status.notin_(_NOT_COUNTED)).order_by(ClinicOutreachMessage.scheduled_for).limit(1))).scalar_one_or_none()
            if existing is not None:  # a draft of this kind exists already: never make a second one; make the stage agree with it
                clinic.stage, clinic.next_action_at = next_stage, _utc(existing) + gap
                await session.commit()
                return _Outcome("skipped")

            def park(reason: str) -> _Outcome:
                clinic.stage, clinic.handoff_reason = OutreachStage.NEEDS_PERSON, reason
                return _Outcome("parked")

            address = (clinic.contact_email or "").strip()
            parked: _Outcome | None = None
            if not address:
                parked = park(NO_ADDRESS)
            elif await ClinicOutreachService.is_suppressed_in(session, tenant_id, address):
                parked = park(SUPPRESSED)
            provider = None
            if parked is None:
                provider = (await session.execute(select(Provider).where(Provider.id == clinic.provider_id, Provider.tenant_id == tenant_id))).scalar_one()
                if timezone_for(provider.country) is None:
                    parked = park(NO_TIME_ZONE)
            if parked is not None:
                await session.commit()
                return parked

            invited = (await session.execute(select(func.max(AuditLog.created_at)).where(
                AuditLog.tenant_id == tenant_id, AuditLog.action == INVITE_ACTION, AuditLog.entity_id == clinic.provider_id,
                AuditLog.created_at >= now - ONBOARDER_QUIET))).scalar_one()
            if invited is not None:
                clinic.next_action_at = _utc(invited) + ONBOARDER_QUIET
                await session.commit()
                return _Outcome("skipped")

            drafted = (await session.execute(select(func.count(ClinicOutreachMessage.id)).where(
                ClinicOutreachMessage.tenant_id == tenant_id, ClinicOutreachMessage.outreach_id == outreach_id, ClinicOutreachMessage.direction == "OUT",
                ClinicOutreachMessage.status.notin_(_NOT_COUNTED)))).scalar_one()
            if drafted >= config.limits.max_emails_per_clinic:
                clinic.stage, clinic.next_action_at = OutreachStage.NO_RESPONSE, None
                await session.commit()
                return _Outcome("done")

            live = cfg_row.mode == OutreachMode.LIVE
            if live and kind == OutreachMessageKind.INTRO and cfg_row.live_at is not None and (now - _utc(cfg_row.live_at)) < FIRST_WAVE_DAYS:
                introduced = (await session.execute(select(func.count(func.distinct(ClinicOutreachMessage.outreach_id)))
                                                    .join(ClinicOutreach, ClinicOutreach.id == ClinicOutreachMessage.outreach_id)
                                                    .where(ClinicOutreach.campaign_id == campaign_id, ClinicOutreachMessage.tenant_id == tenant_id,
                                                           ClinicOutreachMessage.kind == OutreachMessageKind.INTRO.value,
                                                           ClinicOutreachMessage.status.notin_(_NOT_COUNTED)))).scalar_one()
                if introduced >= FIRST_WAVE_CLINICS:
                    return _Outcome("skipped")  # the first live week reaches at most 10 clinics
            earliest = max(now, _utc(clinic.next_action_at)) if clinic.next_action_at is not None else now
            slot = next_send_slot(earliest, provider.country, config.limits, horizon_end=horizon_end)
            if slot is None:
                return _Outcome("skipped")  # no send window before the horizon: left for a later run

            day = _schedule_day(slot)  # the cap is per UTC day of the slot, so hourly runs cannot each spend a fresh allowance
            held = (await session.execute(select(func.count(ClinicOutreachMessage.id))
                                          .join(ClinicOutreach, ClinicOutreach.id == ClinicOutreachMessage.outreach_id)
                                          .where(ClinicOutreach.campaign_id == campaign_id, ClinicOutreachMessage.tenant_id == tenant_id,
                                                 ClinicOutreachMessage.status.notin_(_NOT_COUNTED), ClinicOutreachMessage.direction == "OUT",
                                                 ClinicOutreachMessage.schedule_day == day))).scalar_one()
            if held >= warmup_cap(config.limits, shadow_started_at, slot):
                return _Outcome("day_full")  # that day is full: this clinic waits for a later run (untouched); others may fall on another day

            ref = ClinicRef(name=provider.name)
            subject, body = compose(kind, ref, config.sender, config.term_sheet, blocks)
            problems = check(subject, body, clinic=ref, sender=config.sender, opt_out_line=blocks["opt_out_line"])
            if problems:
                reason = "; ".join(problems)
                stored = await self._insert(session, ClinicOutreachMessage(
                    tenant_id=tenant_id, outreach_id=outreach_id, direction="OUT", kind=kind.value, status=OutreachMessageStatus.BLOCKED, subject=subject[:255],
                    body=body, blocks_version=version, blocked_reason=reason, scheduled_for=now, schedule_day=_schedule_day(now)))
                park(reason)
                await session.commit()
                return _Outcome("blocked" if stored else "parked")

            stored = await self._insert(session, ClinicOutreachMessage(
                tenant_id=tenant_id, outreach_id=outreach_id, direction="OUT", kind=kind.value, status=OutreachMessageStatus.QUEUED_LIVE if live else OutreachMessageStatus.QUEUED_SHADOW,
                subject=subject[:255], body=body, blocks_version=version, scheduled_for=slot, schedule_day=_schedule_day(slot)))
            if not stored:
                return _Outcome("skipped")  # the unique key says it was already done
            clinic.stage, clinic.next_action_at = next_stage, slot + gap  # emails_sent and last_sent_at are real-send counters: never touched here
            await session.commit()
            return _Outcome("composed")

    @staticmethod
    async def _insert(session, message: ClinicOutreachMessage) -> bool:
        """Insert inside a savepoint; a unique violation means 'already done' and leaves the outer transaction usable."""
        try:
            async with session.begin_nested():
                session.add(message)
        except IntegrityError:
            return False
        return True

    # --- failure bookkeeping (no new column: audit rows keyed by the outreach id) ---------------------------------------

    @staticmethod
    def _marker(tenant_id: uuid.UUID, action: str, outreach_id: uuid.UUID, summary: dict[str, Any]) -> AuditLog:
        return AuditLog(tenant_id=tenant_id, actor_type=ActorType.SYSTEM, actor_id=None, action=action, entity_type="clinic_outreach", entity_id=outreach_id,
                        input_summary=summary, result="failure" if action == ERROR_ACTION else "success")

    @staticmethod
    async def _failures_since_success(session, tenant_id: uuid.UUID, outreach_id: uuid.UUID) -> int:
        """Errors recorded for this clinic minus those a later success already cleared (each recovery row stores how many it cleared; no timestamps,
        which are only second-precise on some databases)."""
        errors = (await session.execute(select(func.count(AuditLog.id)).where(
            AuditLog.tenant_id == tenant_id, AuditLog.action == ERROR_ACTION, AuditLog.entity_id == outreach_id))).scalar_one()
        cleared = (await session.execute(select(AuditLog.input_summary).where(
            AuditLog.tenant_id == tenant_id, AuditLog.action == RECOVERED_ACTION, AuditLog.entity_id == outreach_id))).scalars().all()
        return errors - sum(int((c or {}).get("cleared", 0)) for c in cleared)

    async def _clear_failures(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID, outreach_id: uuid.UUID) -> None:
        """After any run that did not raise: if the clinic has uncleared errors, record that they are cleared. Never raises."""
        try:
            async with self._sf() as session:
                await set_tenant_context(session, tenant_id)
                pending = await self._failures_since_success(session, tenant_id, outreach_id)
                if pending > 0:
                    session.add(self._marker(tenant_id, RECOVERED_ACTION, outreach_id, {"campaign_id": str(campaign_id), "cleared": pending}))
                    await session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("outreach_engine_failure_clear_failed", outreach_id=str(outreach_id), error_type=type(exc).__name__)

    async def _record_failure(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID, outreach_id: uuid.UUID) -> bool:
        """Store the failure; after three in a row park the clinic. Returns True when it was parked. Never raises."""
        try:
            async with self._sf() as session:
                await set_tenant_context(session, tenant_id)
                session.add(self._marker(tenant_id, ERROR_ACTION, outreach_id, {"campaign_id": str(campaign_id)}))
                await session.flush()
                parked = await self._failures_since_success(session, tenant_id, outreach_id) >= FAILURES_BEFORE_PARKING
                if parked:
                    clinic = (await session.execute(select(ClinicOutreach).where(
                        ClinicOutreach.id == outreach_id, ClinicOutreach.tenant_id == tenant_id).with_for_update())).scalar_one_or_none()
                    if clinic is not None and clinic.stage in (*_DRAFTING_STAGES, OutreachStage.FOLLOWUP_2.value):
                        clinic.stage, clinic.handoff_reason = OutreachStage.NEEDS_PERSON, COULD_NOT_PROCESS
                    else:
                        parked = False
                await session.commit()
                return parked
        except Exception as exc:  # noqa: BLE001
            logger.warning("outreach_engine_failure_record_failed", outreach_id=str(outreach_id), error_type=type(exc).__name__)
            return False
