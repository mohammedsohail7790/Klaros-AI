import uuid
from decimal import Decimal

from pydantic import BaseModel

from app.models.rbac import Permission
from app.services.referral_service import (
    InvalidReferralStateError,
    ProgramNotFoundError,
    ReferralNotFoundError,
    ReferralService,
    RewardNotFoundError,
)
from app.tools.base import ExecutionContext, Tool


class CreateProgramInput(BaseModel):
    name: str
    reward_type: str = "credit"
    reward_amount: Decimal | None = None


class ProgramOutput(BaseModel):
    program_id: str
    campaign_id: str


class CreateReferralProgram(Tool):
    name = "retention.create_referral_program"
    description = "Create a referral program (backed by a real Campaign, channel=REFERRAL, for attribution)."
    input_schema = CreateProgramInput
    output_schema = ProgramOutput
    required_permission = Permission.MANAGE_REFERRALS

    def __init__(self, referral_service: ReferralService) -> None:
        self._referral_service = referral_service

    async def execute(self, input: CreateProgramInput, context: ExecutionContext) -> ProgramOutput:
        program = await self._referral_service.create_program(
            context.tenant_id, name=input.name, reward_type=input.reward_type, reward_amount=input.reward_amount
        )
        return ProgramOutput(program_id=str(program.id), campaign_id=str(program.campaign_id))


class GetOrCreateCodeInput(BaseModel):
    program_id: uuid.UUID
    customer_id: uuid.UUID


class CodeOutput(BaseModel):
    code_id: str
    code: str


class GetOrCreateReferralCode(Tool):
    name = "retention.get_or_create_referral_code"
    description = "Get or create a customer's referral code for a program (idempotent)."
    input_schema = GetOrCreateCodeInput
    output_schema = CodeOutput
    required_permission = Permission.MANAGE_REFERRALS

    def __init__(self, referral_service: ReferralService) -> None:
        self._referral_service = referral_service

    async def execute(self, input: GetOrCreateCodeInput, context: ExecutionContext) -> CodeOutput:
        try:
            code = await self._referral_service.get_or_create_code(context.tenant_id, input.program_id, input.customer_id)
        except ProgramNotFoundError as e:
            raise ValueError(str(e)) from e
        return CodeOutput(code_id=str(code.id), code=code.code)


class CreateReferralInput(BaseModel):
    referral_code_id: uuid.UUID


class ReferralOutput(BaseModel):
    referral_id: str
    status: str


class CreateReferral(Tool):
    name = "retention.create_referral"
    description = "Record that a referral code was used to refer a new prospect."
    input_schema = CreateReferralInput
    output_schema = ReferralOutput
    required_permission = Permission.MANAGE_REFERRALS

    def __init__(self, referral_service: ReferralService) -> None:
        self._referral_service = referral_service

    async def execute(self, input: CreateReferralInput, context: ExecutionContext) -> ReferralOutput:
        try:
            referral = await self._referral_service.create_referral(context.tenant_id, input.referral_code_id)
        except ReferralNotFoundError as e:
            raise ValueError(str(e)) from e
        return ReferralOutput(referral_id=str(referral.id), status=referral.status)


class ConvertReferralToLeadInput(BaseModel):
    referral_id: uuid.UUID
    name: str
    phone: str | None = None
    email: str | None = None
    service_requested: str | None = None


class ConvertReferralToLead(Tool):
    """Idempotent — retrying never creates a second Lead. Reuses the
    *existing* Lead model (source=REFERRAL) and the *existing* Phase 6
    attribution engine — no second lead, no second attribution system."""

    name = "retention.convert_referral_to_lead"
    description = "Create the real Lead (source=REFERRAL) for a referral and attribute it to the referral program's campaign."
    input_schema = ConvertReferralToLeadInput
    output_schema = ReferralOutput
    required_permission = Permission.MANAGE_REFERRALS

    def __init__(self, referral_service: ReferralService) -> None:
        self._referral_service = referral_service

    async def execute(self, input: ConvertReferralToLeadInput, context: ExecutionContext) -> ReferralOutput:
        try:
            referral = await self._referral_service.convert_referral_to_lead(
                context.tenant_id, input.referral_id, name=input.name, phone=input.phone, email=input.email,
                service_requested=input.service_requested,
            )
        except ReferralNotFoundError as e:
            raise ValueError(str(e)) from e
        return ReferralOutput(referral_id=str(referral.id), status=referral.status)


class RequestRewardInput(BaseModel):
    referral_id: uuid.UUID
    amount: Decimal


class RewardOutput(BaseModel):
    reward_id: str
    status: str


class RequestReferralReward(Tool):
    """Never issues a reward directly — always lands `PENDING` with a real
    `ApprovalRequest` attached. AI can request; AI cannot approve its own
    request (APPROVE_REFERRAL_REWARD is permission-gated)."""

    name = "retention.request_referral_reward"
    description = "Request a referral reward. Always requires human approval before issuance."
    input_schema = RequestRewardInput
    output_schema = RewardOutput
    required_permission = Permission.MANAGE_REFERRALS

    def __init__(self, referral_service: ReferralService) -> None:
        self._referral_service = referral_service

    async def execute(self, input: RequestRewardInput, context: ExecutionContext) -> RewardOutput:
        try:
            reward = await self._referral_service.request_reward(
                context.tenant_id, input.referral_id, amount=input.amount, requested_by=context.actor_id
            )
        except ReferralNotFoundError as e:
            raise ValueError(str(e)) from e
        return RewardOutput(reward_id=str(reward.id), status=reward.status)


class DecideRewardInput(BaseModel):
    reward_id: uuid.UUID


class ApproveReferralReward(Tool):
    """Gated by APPROVE_REFERRAL_REWARD — a human-only permission the AI
    execution boundary's default role does not hold. Resolves the
    ApprovalRequest AND transitions the reward in one call."""

    name = "retention.approve_referral_reward"
    description = "Approve a pending referral reward."
    input_schema = DecideRewardInput
    output_schema = RewardOutput
    required_permission = Permission.APPROVE_REFERRAL_REWARD

    def __init__(self, referral_service: ReferralService) -> None:
        self._referral_service = referral_service

    async def execute(self, input: DecideRewardInput, context: ExecutionContext) -> RewardOutput:
        try:
            reward = await self._referral_service.decide_reward(context.tenant_id, input.reward_id, approved=True, decided_by=context.actor_id)
        except (RewardNotFoundError, InvalidReferralStateError) as e:
            raise ValueError(str(e)) from e
        return RewardOutput(reward_id=str(reward.id), status=reward.status)


class RejectReferralReward(Tool):
    name = "retention.reject_referral_reward"
    description = "Reject a pending referral reward."
    input_schema = DecideRewardInput
    output_schema = RewardOutput
    required_permission = Permission.APPROVE_REFERRAL_REWARD

    def __init__(self, referral_service: ReferralService) -> None:
        self._referral_service = referral_service

    async def execute(self, input: DecideRewardInput, context: ExecutionContext) -> RewardOutput:
        try:
            reward = await self._referral_service.decide_reward(context.tenant_id, input.reward_id, approved=False, decided_by=context.actor_id)
        except (RewardNotFoundError, InvalidReferralStateError) as e:
            raise ValueError(str(e)) from e
        return RewardOutput(reward_id=str(reward.id), status=reward.status)


class IssueRewardInput(BaseModel):
    reward_id: uuid.UUID


class IssueReferralReward(Tool):
    """Internal record only — no real payment provider is ever called."""

    name = "retention.issue_referral_reward"
    description = "Issue an APPROVED referral reward (internal record only)."
    input_schema = IssueRewardInput
    output_schema = RewardOutput
    required_permission = Permission.APPROVE_REFERRAL_REWARD

    def __init__(self, referral_service: ReferralService) -> None:
        self._referral_service = referral_service

    async def execute(self, input: IssueRewardInput, context: ExecutionContext) -> RewardOutput:
        try:
            reward = await self._referral_service.issue_reward(context.tenant_id, input.reward_id)
        except (RewardNotFoundError, InvalidReferralStateError) as e:
            raise ValueError(str(e)) from e
        return RewardOutput(reward_id=str(reward.id), status=reward.status)
