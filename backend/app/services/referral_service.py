"""section: Referral System. Rides the *existing* Phase 6 attribution
engine rather than building a second one — `ReferralProgram.campaign_id`
is a real `Campaign` (channel=REFERRAL), so `AttributionService.
campaign_performance` already reports referral spend/leads/revenue/CAC/
ROAS. `Referral.status` tracks the referral-specific lifecycle in
parallel, driven by the same real events. The referred prospect becomes
the *existing* `Lead` model (via `LeadService`, `source=REFERRAL`) —
never a second lead record. Rewards always start `PENDING`; only a
dedicated, permission-gated `approve_referral_reward` call (never the
generic ToolRegistry `APPROVAL_REQUIRED` path, to avoid the "approval
doesn't resume the action" dead end named in Phase 5/6) can move one to
`ISSUED`, and it never calls a real payment provider.
"""

import secrets
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.events.bus import EventBus
from app.models.crm import LeadSource
from app.models.event import EventType
from app.models.marketing import Campaign, CampaignChannel, CampaignObjective
from app.models.retention import (
    Referral,
    ReferralCode,
    ReferralProgram,
    ReferralProgramStatus,
    ReferralReward,
    ReferralStatus,
    RewardStatus,
)
from app.services.approval_helper import create_approval_request
from app.services.attribution_service import AttributionService
from app.services.campaign_service import CampaignService
from app.services.lead_service import CreateLeadInput, LeadService


class ProgramNotFoundError(Exception):
    pass


class ReferralNotFoundError(Exception):
    pass


class RewardNotFoundError(Exception):
    pass


class InvalidReferralStateError(Exception):
    pass


class ReferralService:
    def __init__(
        self, session_factory: async_sessionmaker, bus: EventBus, campaign_service: CampaignService,
        attribution_service: AttributionService, lead_service: LeadService,
    ) -> None:
        self._session_factory = session_factory
        self._bus = bus
        self._campaign_service = campaign_service
        self._attribution_service = attribution_service
        self._lead_service = lead_service

    async def create_program(
        self, tenant_id: uuid.UUID, *, name: str, reward_type: str = "credit", reward_amount: Decimal | None = None,
    ) -> ReferralProgram:
        campaign = await self._campaign_service.create_campaign(
            tenant_id, name=f"Referral Program: {name}", channel=CampaignChannel.REFERRAL,
            objective=CampaignObjective.LEAD_GEN,
        )
        async with self._session_factory() as session:
            program = ReferralProgram(
                tenant_id=tenant_id, name=name, campaign_id=campaign.id, reward_type=reward_type,
                reward_amount=reward_amount, status=ReferralProgramStatus.ACTIVE,
            )
            session.add(program)
            await session.commit()
            await session.refresh(program)
        return program

    async def get_or_create_code(self, tenant_id: uuid.UUID, program_id: uuid.UUID, customer_id: uuid.UUID) -> ReferralCode:
        async with self._session_factory() as session:
            program = await session.get(ReferralProgram, program_id)
            if program is None or program.tenant_id != tenant_id:
                raise ProgramNotFoundError("Referral program not found")

            existing = (
                await session.execute(
                    select(ReferralCode).where(
                        ReferralCode.tenant_id == tenant_id, ReferralCode.program_id == program_id,
                        ReferralCode.customer_id == customer_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing

            code = None
            for _ in range(5):
                candidate = secrets.token_hex(4).upper()
                clash = (
                    await session.execute(select(ReferralCode).where(ReferralCode.tenant_id == tenant_id, ReferralCode.code == candidate))
                ).scalar_one_or_none()
                if clash is None:
                    code = candidate
                    break
            if code is None:
                raise RuntimeError("Could not generate a unique referral code")

            row = ReferralCode(tenant_id=tenant_id, program_id=program_id, customer_id=customer_id, code=code)
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return row

    async def create_referral(self, tenant_id: uuid.UUID, referral_code_id: uuid.UUID) -> Referral:
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            code = await session.get(ReferralCode, referral_code_id)
            if code is None or code.tenant_id != tenant_id:
                raise ReferralNotFoundError("Referral code not found")

            referral = Referral(
                tenant_id=tenant_id, program_id=code.program_id, referral_code_id=code.id,
                referrer_customer_id=code.customer_id, status=ReferralStatus.CREATED, created_at_referral=now,
            )
            session.add(referral)
            await session.commit()
            await session.refresh(referral)

        await self._bus.publish(
            tenant_id=tenant_id, event_type=EventType.RETENTION_REFERRAL_CREATED, source="retention",
            entity_type="referral", entity_id=referral.id,
            payload={"referrer_customer_id": str(code.customer_id)},
        )
        return referral

    async def convert_referral_to_lead(
        self, tenant_id: uuid.UUID, referral_id: uuid.UUID, *, name: str, phone: str | None = None,
        email: str | None = None, service_requested: str | None = None,
    ) -> Referral:
        """Idempotent: retrying with the same referral_id never creates a
        second lead — `LeadService.create_lead`'s own idempotency_key
        (`referral-{referral_id}`) guarantees that, and this method itself
        no-ops if the referral already has a `lead_id`."""
        async with self._session_factory() as session:
            referral = await session.get(Referral, referral_id)
            if referral is None or referral.tenant_id != tenant_id:
                raise ReferralNotFoundError("Referral not found")
            if referral.lead_id is not None:
                return referral
            program = await session.get(ReferralProgram, referral.program_id)

        lead, _ = await self._lead_service.create_lead(
            tenant_id,
            CreateLeadInput(
                name=name, source=LeadSource.REFERRAL, phone=phone, email=email,
                source_detail=f"referral:{referral_id}", campaign_id=program.campaign_id,
                service_requested=service_requested, idempotency_key=f"referral-{referral_id}",
            ),
        )

        async with self._session_factory() as session:
            referral = await session.get(Referral, referral_id)
            referral.lead_id = lead.id
            referral.status = ReferralStatus.LEAD_CREATED
            await session.commit()
            await session.refresh(referral)

        await self._attribution_service.attribute_lead(
            tenant_id, lead.id, campaign_id=program.campaign_id, source="referral", medium="referral",
            referral_source=f"referral:{referral_id}",
        )

        await self._bus.publish(
            tenant_id=tenant_id, event_type=EventType.RETENTION_REFERRAL_LEAD_CREATED, source="retention",
            entity_type="referral", entity_id=referral.id, payload={"lead_id": str(lead.id)},
        )
        return referral

    async def _advance_status(self, tenant_id: uuid.UUID, lead_id: uuid.UUID, status: str, **fields) -> Referral | None:
        async with self._session_factory() as session:
            referral = (
                await session.execute(select(Referral).where(Referral.tenant_id == tenant_id, Referral.lead_id == lead_id))
            ).scalar_one_or_none()
            if referral is None:
                return None
            _ORDER = [
                ReferralStatus.CREATED, ReferralStatus.CLICKED, ReferralStatus.LEAD_CREATED, ReferralStatus.QUALIFIED,
                ReferralStatus.BOOKED, ReferralStatus.CONVERTED, ReferralStatus.REWARDED,
            ]
            if status in _ORDER and referral.status in _ORDER and _ORDER.index(status) > _ORDER.index(referral.status):
                referral.status = status
            for key, value in fields.items():
                if value is not None:
                    setattr(referral, key, value)
            await session.commit()
            await session.refresh(referral)
        return referral

    async def mark_qualified(self, tenant_id: uuid.UUID, lead_id: uuid.UUID) -> None:
        referral = await self._advance_status(tenant_id, lead_id, ReferralStatus.QUALIFIED)
        if referral:
            await self._bus.publish(
                tenant_id=tenant_id, event_type=EventType.RETENTION_REFERRAL_QUALIFIED, source="retention",
                entity_type="referral", entity_id=referral.id, payload={},
            )

    async def mark_booked(self, tenant_id: uuid.UUID, lead_id: uuid.UUID) -> None:
        referral = await self._advance_status(tenant_id, lead_id, ReferralStatus.BOOKED)
        if referral:
            await self._bus.publish(
                tenant_id=tenant_id, event_type=EventType.RETENTION_REFERRAL_BOOKED, source="retention",
                entity_type="referral", entity_id=referral.id, payload={},
            )

    async def mark_job(self, tenant_id: uuid.UUID, lead_id: uuid.UUID, job_id: uuid.UUID, customer_id: uuid.UUID) -> None:
        await self._advance_status(tenant_id, lead_id, ReferralStatus.BOOKED, job_id=job_id, referred_customer_id=customer_id)

    async def mark_converted(self, tenant_id: uuid.UUID, lead_id: uuid.UUID, invoice_id: uuid.UUID, revenue_amount: Decimal) -> None:
        """Idempotent: re-processing an already-CONVERTED referral for the
        same invoice does not double-fire the event or re-request a
        reward — checked via referral.status before advancing."""
        async with self._session_factory() as session:
            referral = (
                await session.execute(select(Referral).where(Referral.tenant_id == tenant_id, Referral.lead_id == lead_id))
            ).scalar_one_or_none()
            already_converted = referral is not None and referral.status in (ReferralStatus.CONVERTED, ReferralStatus.REWARDED)

        referral = await self._advance_status(
            tenant_id, lead_id, ReferralStatus.CONVERTED, invoice_id=invoice_id, revenue_amount=revenue_amount,
        )
        if referral is None or already_converted:
            return

        await self._bus.publish(
            tenant_id=tenant_id, event_type=EventType.RETENTION_REFERRAL_CONVERTED, source="retention",
            entity_type="referral", entity_id=referral.id,
            payload={"revenue_amount": str(revenue_amount)},
        )

        program = None
        async with self._session_factory() as session:
            program = await session.get(ReferralProgram, referral.program_id)
        if program and program.reward_amount:
            await self.request_reward(tenant_id, referral.id, amount=program.reward_amount, requested_by=None)

    async def mark_collected(self, tenant_id: uuid.UUID, lead_id: uuid.UUID, collected_amount: Decimal) -> None:
        await self._advance_status(tenant_id, lead_id, ReferralStatus.CONVERTED, collected_amount=collected_amount)

    # --- Rewards -------------------------------------------------------------

    async def request_reward(self, tenant_id: uuid.UUID, referral_id: uuid.UUID, *, amount: Decimal, requested_by: uuid.UUID | None) -> ReferralReward:
        async with self._session_factory() as session:
            referral = await session.get(Referral, referral_id)
            if referral is None or referral.tenant_id != tenant_id:
                raise ReferralNotFoundError("Referral not found")

            existing = (
                await session.execute(
                    select(ReferralReward).where(ReferralReward.tenant_id == tenant_id, ReferralReward.referral_id == referral_id)
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing

            reward = ReferralReward(
                tenant_id=tenant_id, referral_id=referral_id, customer_id=referral.referrer_customer_id,
                amount=amount, status=RewardStatus.PENDING, requested_by=requested_by,
            )
            session.add(reward)
            await session.flush()

            await create_approval_request(
                session, tenant_id=tenant_id, requested_by_type="USER", requested_by_id=requested_by,
                tool_name="retention.approve_referral_reward", action_type="referral_reward_approval",
                reason=f"Referral reward of ${amount} requested for referrer {referral.referrer_customer_id}",
                tool_input={"reward_id": str(reward.id)},
            )
            await session.commit()
            await session.refresh(reward)

        await self._bus.publish(
            tenant_id=tenant_id, event_type=EventType.RETENTION_REWARD_REQUESTED, source="retention",
            entity_type="referral_reward", entity_id=reward.id, payload={"amount": str(amount)},
        )
        return reward

    async def decide_reward(self, tenant_id: uuid.UUID, reward_id: uuid.UUID, *, approved: bool, decided_by: uuid.UUID | None) -> ReferralReward:
        """Resolves the ApprovalRequest AND completes the reward issuance
        in one call — the dedicated-tool pattern Phase 5 established for
        `finance.approve_invoice`, avoiding the generic approval dead end."""
        from app.models.approval import ApprovalRequest, ApprovalStatus

        async with self._session_factory() as session:
            reward = await session.get(ReferralReward, reward_id)
            if reward is None or reward.tenant_id != tenant_id:
                raise RewardNotFoundError("Referral reward not found")
            if reward.status != RewardStatus.PENDING:
                raise InvalidReferralStateError("Reward is not PENDING")

            pending = (
                await session.execute(
                    select(ApprovalRequest).where(
                        ApprovalRequest.tenant_id == tenant_id, ApprovalRequest.tool_name == "retention.approve_referral_reward",
                        ApprovalRequest.status == ApprovalStatus.PENDING,
                    )
                )
            ).scalars().all()
            for req in pending:
                if req.tool_input.get("reward_id") == str(reward_id):
                    req.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
                    req.decided_by = decided_by

            if approved:
                reward.status = RewardStatus.APPROVED
                reward.approved_by = decided_by
            else:
                reward.status = RewardStatus.CANCELLED
            await session.commit()
            await session.refresh(reward)

        await self._bus.publish(
            tenant_id=tenant_id, event_type=EventType.RETENTION_REWARD_APPROVED, source="retention",
            entity_type="referral_reward", entity_id=reward.id, payload={"approved": approved},
        )
        return reward

    async def issue_reward(self, tenant_id: uuid.UUID, reward_id: uuid.UUID) -> ReferralReward:
        """Internal record only — no real payment provider is ever
        called."""
        async with self._session_factory() as session:
            reward = await session.get(ReferralReward, reward_id)
            if reward is None or reward.tenant_id != tenant_id:
                raise RewardNotFoundError("Referral reward not found")
            if reward.status != RewardStatus.APPROVED:
                raise InvalidReferralStateError("Reward must be APPROVED before it can be issued")
            reward.status = RewardStatus.ISSUED

            referral = await session.get(Referral, reward.referral_id)
            if referral is not None:
                referral.status = ReferralStatus.REWARDED

            await session.commit()
            await session.refresh(reward)

        await self._bus.publish(
            tenant_id=tenant_id, event_type=EventType.RETENTION_REWARD_ISSUED, source="retention",
            entity_type="referral_reward", entity_id=reward.id, payload={},
        )
        return reward
