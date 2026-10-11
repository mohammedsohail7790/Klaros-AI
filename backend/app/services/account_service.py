"""Account lifecycle beyond register/login: sign in with just an email, forgot/reset password, change password.

Security properties kept deliberately simple and testable:
  * A reset request ALWAYS answers the same way whether or not the email exists (no account enumeration).
  * A reset token is a 256-bit random secret, stored only as its SHA-256, single use, expiring after RESET_TOKEN_TTL; using it (or requesting a newer one)
    retires every other outstanding token for that user.
  * Resetting or changing a password bumps `token_version`, which instantly invalidates every existing access and refresh token for that user.
  * Signing in by email alone verifies the password against each workspace that email belongs to and only then reveals that more than one matched, so
    workspace names are never disclosed to someone who does not hold the password.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import hash_password, verify_password
from app.db.session import set_tenant_context
from app.models.account import AccountDirectory, PasswordResetToken
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.organization import Organization
from app.models.user import User
from app.services.auth_service import AuthError, authenticate
from app.services.system_mailer import send_system_email

logger = structlog.get_logger(__name__)

RESET_TOKEN_TTL = timedelta(minutes=60)


@dataclass
class Workspace:
    name: str
    slug: str


class AmbiguousWorkspaceError(Exception):
    """The email and password match more than one workspace; the caller must say which."""

    def __init__(self, workspaces: list[Workspace]) -> None:
        super().__init__("multiple_workspaces")
        self.workspaces = workspaces


def _hash_token(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


async def record_directory(db: AsyncSession, *, email: str, tenant_id: uuid.UUID, organization_slug: str) -> None:
    """Remember that this email belongs to this workspace (idempotent). Called wherever a user is created."""
    email = email.lower()
    existing = (
        await db.execute(select(AccountDirectory).where(AccountDirectory.email == email, AccountDirectory.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if existing is None:
        db.add(AccountDirectory(email=email, tenant_id=tenant_id, organization_slug=organization_slug))


async def _workspaces_for(db: AsyncSession, email: str) -> list[AccountDirectory]:
    rows = (await db.execute(select(AccountDirectory).where(AccountDirectory.email == email.lower()))).scalars().all()
    return list(rows)


async def authenticate_by_email(db: AsyncSession, *, email: str, password: str) -> User:
    matches: list[tuple[User, str]] = []
    for entry in await _workspaces_for(db, email):
        try:
            user = await authenticate(db, organization_slug=entry.organization_slug, email=email, password=password)
        except AuthError:
            continue
        matches.append((user, entry.organization_slug))
    if not matches:
        raise AuthError("Invalid email or password")
    if len(matches) > 1:
        workspaces = []
        for user, slug in matches:
            org = await db.get(Organization, user.tenant_id)
            workspaces.append(Workspace(name=org.name if org else slug, slug=slug))
        raise AmbiguousWorkspaceError(workspaces)
    return matches[0][0]


async def request_password_reset(db: AsyncSession, *, email: str) -> None:
    """Creates a reset token for every active account behind this email and emails the link. Never reveals whether anything was found."""
    settings = get_settings()
    now = datetime.now(UTC)
    outgoing: list[tuple[str, str, str]] = []  # (to, workspace name, link)
    for entry in await _workspaces_for(db, email):
        await set_tenant_context(db, entry.tenant_id)
        user = (await db.execute(select(User).where(User.tenant_id == entry.tenant_id, User.email == email.lower()))).scalar_one_or_none()
        if user is None or not user.is_active:
            continue
        await db.execute(
            update(PasswordResetToken).where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None)).values(used_at=now)
        )
        secret = secrets.token_urlsafe(32)
        db.add(PasswordResetToken(user_id=user.id, tenant_id=user.tenant_id, token_hash=_hash_token(secret), expires_at=now + RESET_TOKEN_TTL))
        org = await db.get(Organization, user.tenant_id)
        base = settings.FRONTEND_BASE_URL.rstrip("/")
        outgoing.append((user.email, org.name if org else "your workspace", f"{base}/reset-password?token={secret}"))
    await db.commit()
    for to, workspace, link in outgoing:
        body = (
            f"Hello,\n\nSomeone asked to reset the password for your Klaros AI account on the workspace \"{workspace}\".\n\n"
            f"Choose a new password here (the link works once and expires in 60 minutes):\n{link}\n\n"
            "If you did not ask for this, you can ignore this email: your password has not changed.\n\n- Klaros AI"
        )
        await send_system_email(to=to, subject="Reset your Klaros AI password", body=body)


async def reset_password(db: AsyncSession, *, token: str, new_password: str) -> None:
    now = datetime.now(UTC)
    row = (await db.execute(select(PasswordResetToken).where(PasswordResetToken.token_hash == _hash_token(token)))).scalar_one_or_none()
    expires = row.expires_at.replace(tzinfo=UTC) if row is not None and row.expires_at.tzinfo is None else (row.expires_at if row else None)
    if row is None or row.used_at is not None or expires is None or expires < now:
        raise AuthError("This reset link is invalid or has expired. Ask for a new one.")
    await set_tenant_context(db, row.tenant_id)
    user = (await db.execute(select(User).where(User.id == row.user_id, User.tenant_id == row.tenant_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise AuthError("This reset link is invalid or has expired. Ask for a new one.")
    user.hashed_password = hash_password(new_password)
    user.token_version += 1
    await db.execute(update(PasswordResetToken).where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None)).values(used_at=now))
    db.add(AuditLog(tenant_id=user.tenant_id, actor_type=ActorType.USER, actor_id=user.id, action="user.password_reset", entity_type="user", entity_id=user.id, result="success"))
    await db.commit()


async def change_password(db: AsyncSession, *, user_id: uuid.UUID, current_password: str, new_password: str) -> User:
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or not verify_password(current_password, user.hashed_password):
        raise AuthError("Your current password is not correct.")
    user.hashed_password = hash_password(new_password)
    user.token_version += 1
    db.add(AuditLog(tenant_id=user.tenant_id, actor_type=ActorType.USER, actor_id=user.id, action="user.password_changed", entity_type="user", entity_id=user.id, result="success"))
    await db.commit()
    await db.refresh(user)
    return user
