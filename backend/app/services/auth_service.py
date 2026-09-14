import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, create_refresh_token, hash_password, verify_password
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.organization import Organization
from app.models.rbac import Role
from app.models.user import User


class AuthError(Exception):
    pass


def _slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return base or "org"


async def _unique_slug(db: AsyncSession, name: str) -> str:
    base = _slugify(name)
    slug = base
    suffix = 1
    while (await db.execute(select(Organization).where(Organization.slug == slug))).scalar_one_or_none():
        suffix += 1
        slug = f"{base}-{suffix}"
    return slug


async def register_organization(
    db: AsyncSession, *, organization_name: str, full_name: str, email: str, password: str
) -> tuple[Organization, User]:
    slug = await _unique_slug(db, organization_name)
    # Every new tenant starts on a real 14-day full-access trial (Growth-
    # tier limits, i.e. none) — see app/services/billing_service.py.
    org = Organization(
        name=organization_name,
        slug=slug,
        plan="growth",
        billing_status="trialing",
        trial_ends_at=datetime.now(UTC) + timedelta(days=14),
    )
    db.add(org)
    await db.flush()

    user = User(
        tenant_id=org.id,
        email=email.lower(),
        hashed_password=hash_password(password),
        full_name=full_name,
        role=Role.OWNER,
    )
    db.add(user)
    await db.flush()

    db.add(
        AuditLog(
            tenant_id=org.id,
            actor_type=ActorType.USER,
            actor_id=user.id,
            action="organization.created",
            entity_type="organization",
            entity_id=org.id,
            result="success",
        )
    )
    await db.commit()
    await db.refresh(org)
    await db.refresh(user)

    # Seed the Knowledge Layer with real, editable placeholder files so a
    # brand-new tenant sees the concept immediately rather than an empty
    # page nobody discovers. Uses the same session/session_factory the rest
    # of registration ran on — KnowledgeService.seed_defaults() opens its
    # own session internally (it's a plain service call, not part of this
    # function's transaction), which is fine: registration has already
    # committed by this point, so there's no partial-org-without-a-user
    # state this could ever leave behind.
    from app.db.session import async_session_maker
    from app.services.knowledge_service import KnowledgeService

    await KnowledgeService(async_session_maker).seed_defaults(org.id)

    return org, user


async def authenticate(
    db: AsyncSession, *, organization_slug: str, email: str, password: str
) -> User:
    org = (
        await db.execute(select(Organization).where(Organization.slug == organization_slug))
    ).scalar_one_or_none()
    if org is None:
        raise AuthError("Invalid organization, email, or password")

    user = (
        await db.execute(
            select(User).where(User.tenant_id == org.id, User.email == email.lower())
        )
    ).scalar_one_or_none()

    if user is None or not user.is_active or not verify_password(password, user.hashed_password):
        raise AuthError("Invalid organization, email, or password")

    return user


def issue_tokens(user: User) -> tuple[str, str]:
    access = create_access_token(user.id, user.tenant_id, user.role, user.token_version)
    refresh = create_refresh_token(user.id, user.tenant_id, user.token_version)
    return access, refresh


async def refresh_access_token(db: AsyncSession, *, refresh_payload: dict) -> tuple[str, str]:
    """Exchanges a valid, unrevoked refresh token for a new access token
    (and rotates the refresh token too — one-time-use, so a stolen refresh
    token that's already been redeemed by its rightful owner is worthless
    to an attacker who intercepts it after the fact). Raises AuthError for
    every failure mode — unknown user, deactivated user, or a token_version
    that no longer matches (already logged out / revoked elsewhere)."""
    import uuid as _uuid

    user = (await db.execute(select(User).where(User.id == _uuid.UUID(refresh_payload["sub"])))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise AuthError("Invalid refresh token")
    if refresh_payload.get("ver", 0) != user.token_version:
        raise AuthError("Refresh token has been revoked")
    return issue_tokens(user)


async def logout(db: AsyncSession, *, user_id) -> None:
    """Bumps the user's token_version — every access and refresh token
    issued before this call, no matter its expiry, is instantly rejected
    by `get_current_user`/`refresh_access_token` from here on. This is the
    real revocation mechanism a stateless-only JWT design (every phase
    before Phase 12) had no way to provide short of rotating the app's
    entire JWT_SECRET."""
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        return
    user.token_version += 1
    await db.commit()
