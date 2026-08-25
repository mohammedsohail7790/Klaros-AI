import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, create_refresh_token, hash_password, verify_password
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
    org = Organization(name=organization_name, slug=slug)
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
            actor_type="human",
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
    access = create_access_token(user.id, user.tenant_id, user.role)
    refresh = create_refresh_token(user.id, user.tenant_id)
    return access, refresh
