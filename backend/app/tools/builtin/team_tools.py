"""Team management — invite/list/update members, list/revoke pending
invites. Gated behind Permission.MANAGE_USERS, which (per
app/models/rbac.py's ROLE_PERMISSIONS matrix) only OWNER/ADMIN carry —
the existing permission was already defined but never referenced by any
route until this file.
"""

import uuid
from typing import Any

from pydantic import BaseModel

from app.models.rbac import Permission
from app.models.user import TeamInvite, User
from app.services.team_service import (
    InvalidInviteError,
    InvalidRoleError,
    InviteAlreadyExistsError,
    InviteNotFoundError,
    LastOwnerError,
    TeamService,
    UserNotFoundError,
)
from app.tools.base import ExecutionContext, Tool


def _user_to_dict(u: User) -> dict[str, Any]:
    return {
        "id": str(u.id), "email": u.email, "full_name": u.full_name, "role": u.role,
        "is_active": u.is_active, "created_at": u.created_at.isoformat(),
    }


def _invite_to_dict(i: TeamInvite) -> dict[str, Any]:
    return {
        "id": str(i.id), "email": i.email, "role": i.role, "status": i.status,
        "created_at": i.created_at.isoformat(), "expires_at": i.expires_at.isoformat(),
    }


class ListMembersInput(BaseModel):
    pass


class ListMembersOutput(BaseModel):
    members: list[dict[str, Any]]


class ListMembers(Tool):
    name = "team.list_members"
    description = "List every user in this tenant."
    input_schema = ListMembersInput
    output_schema = ListMembersOutput
    required_permission = Permission.MANAGE_USERS

    def __init__(self, team_service: TeamService) -> None:
        self._team_service = team_service

    async def execute(self, input: ListMembersInput, context: ExecutionContext) -> ListMembersOutput:
        members = await self._team_service.list_members(context.tenant_id)
        return ListMembersOutput(members=[_user_to_dict(m) for m in members])


class UpdateMemberInput(BaseModel):
    user_id: uuid.UUID
    role: str | None = None
    is_active: bool | None = None


class UpdateMemberOutput(BaseModel):
    member: dict[str, Any]


class UpdateMember(Tool):
    name = "team.update_member"
    description = "Change a team member's role or active status."
    input_schema = UpdateMemberInput
    output_schema = UpdateMemberOutput
    required_permission = Permission.MANAGE_USERS

    def __init__(self, team_service: TeamService) -> None:
        self._team_service = team_service

    async def execute(self, input: UpdateMemberInput, context: ExecutionContext) -> UpdateMemberOutput:
        try:
            member = await self._team_service.update_member(
                context.tenant_id, input.user_id, role=input.role, is_active=input.is_active
            )
        except (UserNotFoundError, LastOwnerError, InvalidRoleError) as e:
            raise ValueError(str(e)) from e
        return UpdateMemberOutput(member=_user_to_dict(member))


class CreateInviteInput(BaseModel):
    email: str
    role: str


class CreateInviteOutput(BaseModel):
    invite: dict[str, Any]
    invite_url_path: str
    email_sent: bool


class CreateInvite(Tool):
    name = "team.create_invite"
    description = "Invite a new team member by email, with a real, revocable invite link."
    input_schema = CreateInviteInput
    output_schema = CreateInviteOutput
    required_permission = Permission.MANAGE_USERS

    def __init__(self, team_service: TeamService) -> None:
        self._team_service = team_service

    async def execute(self, input: CreateInviteInput, context: ExecutionContext) -> CreateInviteOutput:
        try:
            invite, token, email_sent = await self._team_service.create_invite(
                context.tenant_id, email=input.email, role=input.role, invited_by=context.actor_id
            )
        except (InviteAlreadyExistsError, InvalidRoleError) as e:
            raise ValueError(str(e)) from e
        return CreateInviteOutput(
            invite=_invite_to_dict(invite), invite_url_path=f"/accept-invite?token={token}", email_sent=email_sent
        )


class ListInvitesInput(BaseModel):
    pass


class ListInvitesOutput(BaseModel):
    invites: list[dict[str, Any]]


class ListInvites(Tool):
    name = "team.list_invites"
    description = "List pending team invites for this tenant."
    input_schema = ListInvitesInput
    output_schema = ListInvitesOutput
    required_permission = Permission.MANAGE_USERS

    def __init__(self, team_service: TeamService) -> None:
        self._team_service = team_service

    async def execute(self, input: ListInvitesInput, context: ExecutionContext) -> ListInvitesOutput:
        invites = await self._team_service.list_invites(context.tenant_id)
        return ListInvitesOutput(invites=[_invite_to_dict(i) for i in invites])


class RevokeInviteInput(BaseModel):
    invite_id: uuid.UUID


class RevokeInviteOutput(BaseModel):
    invite: dict[str, Any]


class RevokeInvite(Tool):
    name = "team.revoke_invite"
    description = "Revoke a pending team invite — the link stops working immediately."
    input_schema = RevokeInviteInput
    output_schema = RevokeInviteOutput
    required_permission = Permission.MANAGE_USERS

    def __init__(self, team_service: TeamService) -> None:
        self._team_service = team_service

    async def execute(self, input: RevokeInviteInput, context: ExecutionContext) -> RevokeInviteOutput:
        try:
            invite = await self._team_service.revoke_invite(context.tenant_id, input.invite_id)
        except (InviteNotFoundError, InvalidInviteError) as e:
            raise ValueError(str(e)) from e
        return RevokeInviteOutput(invite=_invite_to_dict(invite))
