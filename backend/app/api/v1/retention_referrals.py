import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.models.retention import Referral, ReferralCode, ReferralProgram, ReferralReward
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/retention/referrals", tags=["retention-referrals"])


def _program_to_dict(p: ReferralProgram) -> dict[str, Any]:
    return {
        "id": str(p.id), "name": p.name, "campaign_id": str(p.campaign_id), "reward_type": p.reward_type,
        "reward_amount": str(p.reward_amount) if p.reward_amount is not None else None, "status": p.status,
    }


def _referral_to_dict(r: Referral) -> dict[str, Any]:
    return {
        "id": str(r.id), "program_id": str(r.program_id), "referrer_customer_id": str(r.referrer_customer_id),
        "lead_id": str(r.lead_id) if r.lead_id else None, "referred_customer_id": str(r.referred_customer_id) if r.referred_customer_id else None,
        "status": r.status, "revenue_amount": str(r.revenue_amount) if r.revenue_amount is not None else None,
        "collected_amount": str(r.collected_amount) if r.collected_amount is not None else None,
    }


def _reward_to_dict(r: ReferralReward) -> dict[str, Any]:
    return {"id": str(r.id), "referral_id": str(r.referral_id), "customer_id": str(r.customer_id), "amount": str(r.amount), "status": r.status}


@router.get("/programs")
async def list_programs(current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    rows = (await db.execute(select(ReferralProgram).where(ReferralProgram.tenant_id == current_user.tenant_id))).scalars().all()
    return {"programs": [_program_to_dict(p) for p in rows]}


@router.post("/programs")
async def create_program(body: dict, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    """body = {name, reward_type?, reward_amount?}"""
    try:
        output = await registry.execute("retention.create_referral_program", body, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("/codes")
async def list_codes(program_id: uuid.UUID | None = None, current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    query = select(ReferralCode).where(ReferralCode.tenant_id == current_user.tenant_id)
    if program_id:
        query = query.where(ReferralCode.program_id == program_id)
    rows = (await db.execute(query)).scalars().all()
    return {"codes": [{"id": str(c.id), "program_id": str(c.program_id), "customer_id": str(c.customer_id), "code": c.code} for c in rows]}


@router.post("/codes")
async def get_or_create_code(body: dict, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    """body = {program_id, customer_id}"""
    try:
        output = await registry.execute("retention.get_or_create_referral_code", body, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("")
async def list_referrals(current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    rows = (await db.execute(select(Referral).where(Referral.tenant_id == current_user.tenant_id).order_by(Referral.created_at.desc()))).scalars().all()
    return {"referrals": [_referral_to_dict(r) for r in rows]}


@router.post("")
async def create_referral(body: dict, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    """body = {referral_code_id}"""
    try:
        output = await registry.execute("retention.create_referral", body, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/{referral_id}/convert-to-lead")
async def convert_to_lead(referral_id: uuid.UUID, body: dict, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    """body = {name, phone?, email?, service_requested?}"""
    payload = {"referral_id": str(referral_id), **body}
    try:
        output = await registry.execute("retention.convert_referral_to_lead", payload, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("/rewards")
async def list_rewards(current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    rows = (await db.execute(select(ReferralReward).where(ReferralReward.tenant_id == current_user.tenant_id))).scalars().all()
    return {"rewards": [_reward_to_dict(r) for r in rows]}


@router.post("/rewards")
async def request_reward(body: dict, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    """body = {referral_id, amount}"""
    try:
        output = await registry.execute("retention.request_referral_reward", body, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/rewards/{reward_id}/approve")
async def approve_reward(reward_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    try:
        output = await registry.execute("retention.approve_referral_reward", {"reward_id": str(reward_id)}, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/rewards/{reward_id}/reject")
async def reject_reward(reward_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    try:
        output = await registry.execute("retention.reject_referral_reward", {"reward_id": str(reward_id)}, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/rewards/{reward_id}/issue")
async def issue_reward(reward_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    try:
        output = await registry.execute("retention.issue_referral_reward", {"reward_id": str(reward_id)}, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")
