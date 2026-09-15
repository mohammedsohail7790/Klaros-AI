"""The public, UNAUTHENTICATED counterpart to /users/invites — where an
invited person actually lands. Trust boundary is the signed
`team_invite` token (app/core/security.py::create_invite_token/
decode_invite_token), same pattern as public_quotes.py/
public_contracts.py: tenant_id/email/role are read ONLY from the
verified token payload, and TeamService further requires the underlying
TeamInvite row to still be PENDING and unexpired — a revoked invite's
token verifies fine but is rejected at that layer.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.communications.factory import get_communication_provider
from app.core.rate_limit import rate_limit
from app.db.session import async_session_maker
from app.schemas.auth import TokenResponse, UserResponse
from app.services.auth_service import issue_tokens
from app.services.team_service import InvalidInviteError, TeamService

router = APIRouter(prefix="/public/invites", tags=["public-invites"])

_rate_limit_dependency = Depends(
    rate_limit("public_invite", limit_setting="RATE_LIMIT_PUBLIC_INVITE_PER_MINUTE", window_seconds=60)
)


def _team_service() -> TeamService:
    return TeamService(async_session_maker, get_communication_provider(async_session_maker))


@router.get("/{token}", dependencies=[_rate_limit_dependency])
async def preview_invite(token: str, service: TeamService = Depends(_team_service)) -> dict[str, Any]:
    try:
        preview = await service.preview_invite(token)
    except InvalidInviteError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"organization_name": preview.organization_name, "email": preview.email, "role": preview.role}


class AcceptInviteRequest(BaseModel):
    full_name: str
    password: str


class AcceptInviteResponse(BaseModel):
    user: UserResponse
    tokens: TokenResponse


@router.post("/{token}/accept", dependencies=[_rate_limit_dependency])
async def accept_invite(
    token: str, body: AcceptInviteRequest, service: TeamService = Depends(_team_service)
) -> AcceptInviteResponse:
    try:
        user = await service.accept_invite(token, full_name=body.full_name, password=body.password)
    except InvalidInviteError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    access, refresh = issue_tokens(user)
    return AcceptInviteResponse(
        user=UserResponse.model_validate(user), tokens=TokenResponse(access_token=access, refresh_token=refresh)
    )
