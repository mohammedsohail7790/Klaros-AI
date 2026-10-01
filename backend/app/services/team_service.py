"""Team management — the piece that was completely missing until now:
`register_organization` (app/services/auth_service.py) has always
created exactly one User (role=OWNER) with no path to a second. RBAC
roles (app/models/rbac.py) were always real and enforced everywhere, but
nothing could ever grant them to anyone but the founder.

An invite is a real, DB-backed TeamInvite row (app/models/user.py), not
a bare token — a tenant can see, list, and revoke outstanding invites,
and a second invite to an already-invited email is rejected rather than
silently duplicated. The bearer credential a recipient holds is a
separate signed JWT (app/core/security.py::create_invite_token); the
token alone is never sufficient, the row must still be PENDING and
unexpired at accept time, so revoking a sent link actually works.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.communications.base import CommunicationProvider, MessageTemplate
from app.db.session import set_tenant_context
from app.core.security import TokenError, create_invite_token, decode_invite_token, hash_password
from app.models.organization import Organization
from app.models.rbac import Role
from app.models.user import InviteStatus, TeamInvite, User

_INVITE_EXPIRY_DAYS = 7


class UserNotFoundError(Exception):
    pass


class InviteNotFoundError(Exception):
    pass


class InviteAlreadyExistsError(Exception):
    pass


class InvalidInviteError(Exception):
    pass


class InvalidRoleError(Exception):
    pass


class LastOwnerError(Exception):
    """A tenant must always have at least one active OWNER — refused when
    an update would leave zero."""

    pass


@dataclass
class InvitePreview:
    organization_name: str
    email: str
    role: str


class TeamService:
    def __init__(self, session_factory: async_sessionmaker, comms: CommunicationProvider | None = None) -> None:
        self._session_factory = session_factory
        self._comms = comms

    async def list_members(self, tenant_id: uuid.UUID) -> list[User]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            rows = (
                await session.execute(select(User).where(User.tenant_id == tenant_id).order_by(User.created_at))
            ).scalars().all()
            return list(rows)

    async def _active_owner_count(self, session, tenant_id: uuid.UUID, *, excluding: uuid.UUID | None = None) -> int:
        query = select(func.count()).select_from(User).where(
            User.tenant_id == tenant_id, User.role == Role.OWNER, User.is_active.is_(True)
        )
        if excluding is not None:
            query = query.where(User.id != excluding)
        return (await session.execute(query)).scalar_one()

    async def update_member(
        self, tenant_id: uuid.UUID, user_id: uuid.UUID, *, role: str | None = None, is_active: bool | None = None
    ) -> User:
        if role is not None:
            try:
                Role(role)
            except ValueError as exc:
                raise InvalidRoleError(f"Invalid role: {role}") from exc

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            user = await session.get(User, user_id)
            if user is None or user.tenant_id != tenant_id:
                raise UserNotFoundError("Team member not found")

            would_lose_owner_status = (role is not None and role != Role.OWNER and user.role == Role.OWNER) or (
                is_active is False and user.role == Role.OWNER
            )
            if would_lose_owner_status:
                remaining = await self._active_owner_count(session, tenant_id, excluding=user.id)
                if remaining == 0:
                    raise LastOwnerError("This tenant must always have at least one active owner")

            if role is not None:
                user.role = role
            if is_active is not None:
                user.is_active = is_active
                if not is_active:
                    user.token_version += 1  # instantly revokes any outstanding session

            await session.commit()
            await session.refresh(user)
            return user

    async def create_invite(
        self, tenant_id: uuid.UUID, *, email: str, role: str, invited_by: uuid.UUID
    ) -> tuple[TeamInvite, str, bool]:
        try:
            Role(role)
        except ValueError as exc:
            raise InvalidRoleError(f"Invalid role: {role}") from exc

        normalized_email = email.strip().lower()
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            existing_user = (
                await session.execute(
                    select(User).where(User.tenant_id == tenant_id, User.email == normalized_email)
                )
            ).scalar_one_or_none()
            if existing_user is not None:
                raise InviteAlreadyExistsError(f"{normalized_email} is already a member of this team")

            existing_invite = (
                await session.execute(
                    select(TeamInvite).where(
                        TeamInvite.tenant_id == tenant_id, TeamInvite.email == normalized_email,
                        TeamInvite.status == InviteStatus.PENDING,
                    )
                )
            ).scalar_one_or_none()
            if existing_invite is not None:
                raise InviteAlreadyExistsError(f"{normalized_email} already has a pending invite")

            org = await session.get(Organization, tenant_id)
            org_name = org.name if org else "your team"

            invite = TeamInvite(
                tenant_id=tenant_id, email=normalized_email, role=role, status=InviteStatus.PENDING,
                invited_by=invited_by, expires_at=datetime.now(UTC) + timedelta(days=_INVITE_EXPIRY_DAYS),
            )
            session.add(invite)
            await session.commit()
            await session.refresh(invite)

        token = create_invite_token(invite.id, tenant_id, normalized_email, role)

        email_sent = False
        if self._comms is not None:
            email_sent = await self._comms.send_email(
                tenant_id, to=normalized_email,
                subject=f"You've been invited to join {org_name} on Klaros",
                body=(
                    f"You've been invited to join {org_name} on Klaros as {role.title()}. "
                    f"Use the link you were given to set up your account."
                ),
                template=MessageTemplate.TEAM_INVITE,
            )

        return invite, token, email_sent

    async def list_invites(self, tenant_id: uuid.UUID) -> list[TeamInvite]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            rows = (
                await session.execute(
                    select(TeamInvite)
                    .where(TeamInvite.tenant_id == tenant_id, TeamInvite.status == InviteStatus.PENDING)
                    .order_by(TeamInvite.created_at.desc())
                )
            ).scalars().all()
            return list(rows)

    async def revoke_invite(self, tenant_id: uuid.UUID, invite_id: uuid.UUID) -> TeamInvite:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            invite = await session.get(TeamInvite, invite_id)
            if invite is None or invite.tenant_id != tenant_id:
                raise InviteNotFoundError("Invite not found")
            if invite.status != InviteStatus.PENDING:
                raise InvalidInviteError("Only a pending invite can be revoked")
            invite.status = InviteStatus.REVOKED
            await session.commit()
            await session.refresh(invite)
            return invite

    async def _resolve_pending_invite(self, session, token: str) -> TeamInvite:
        try:
            payload = decode_invite_token(token)
        except TokenError as exc:
            raise InvalidInviteError("This invite link is invalid or has expired") from exc

        invite = await session.get(TeamInvite, uuid.UUID(payload["invite_id"]))
        if invite is None or str(invite.tenant_id) != payload["tenant_id"] or invite.email != payload["email"]:
            raise InvalidInviteError("This invite link is invalid or has expired")
        if invite.status != InviteStatus.PENDING:
            raise InvalidInviteError("This invite has already been used or was revoked")
        if invite.expires_at.replace(tzinfo=UTC) < datetime.now(UTC):
            raise InvalidInviteError("This invite link has expired")
        return invite

    async def preview_invite(self, token: str) -> InvitePreview:
        """`token` is this codebase's own signed invite JWT
        (`create_invite_token`/`decode_invite_token`) — its `tenant_id`
        claim is cryptographically verified before `_resolve_pending_invite`
        ever returns, and is cross-checked again against the real
        `TeamInvite` row's own `tenant_id` column. Context is set only
        AFTER that verification succeeds (not before, when no trusted
        tenant identity yet exists) — the same "resolve first, then stamp"
        shape as `McpCredentialService.authenticate`/the Stripe webhook
        dedup lookup, just with a verified token instead of a hashed
        credential."""
        async with self._session_factory() as session:
            invite = await self._resolve_pending_invite(session, token)
            await set_tenant_context(session, invite.tenant_id)
            org = await session.get(Organization, invite.tenant_id)
            return InvitePreview(
                organization_name=org.name if org else "your team", email=invite.email, role=invite.role
            )

    async def accept_invite(self, token: str, *, full_name: str, password: str) -> User:
        async with self._session_factory() as session:
            invite = await self._resolve_pending_invite(session, token)
            await set_tenant_context(session, invite.tenant_id)

            already_a_user = (
                await session.execute(
                    select(User).where(User.tenant_id == invite.tenant_id, User.email == invite.email)
                )
            ).scalar_one_or_none()
            if already_a_user is not None:
                raise InvalidInviteError(f"{invite.email} is already a member of this team")

            user = User(
                tenant_id=invite.tenant_id, email=invite.email, hashed_password=hash_password(password),
                full_name=full_name, role=invite.role, is_active=True,
            )
            session.add(user)
            invite.status = InviteStatus.ACCEPTED
            invite.accepted_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(user)
            return user
