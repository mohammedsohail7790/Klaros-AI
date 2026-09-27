import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import TokenError, decode_token
from app.db.session import get_db, set_tenant_context
from app.models.rbac import Permission, Role, role_has_permission
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


@dataclass
class CurrentUser:
    id: uuid.UUID
    tenant_id: uuid.UUID
    role: Role


async def get_current_user(
    token: str | None = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)
) -> CurrentUser:
    if token is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = decode_token(token)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
        ) from exc

    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    user_id = uuid.UUID(payload["sub"])

    # Real revocation (Phase 12 production hardening): a purely stateless
    # JWT can't be un-issued short of rotating the app-wide secret. This one
    # indexed lookup per request is the tradeoff for actually being able to
    # log a user out / revoke a compromised token — see
    # app/services/auth_service.py's logout().
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    if payload.get("ver", 0) != user.token_version:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked")

    tenant_id = uuid.UUID(payload["tenant_id"])

    # Phase 0 (KLAROS_PHASE_0_IMPLEMENTATION_PLAN.md §0.2): this is the one
    # place every authenticated request's tenant identity is established,
    # regardless of whether an endpoint takes its DB session via `get_db`
    # or `get_tenant_db` below — FastAPI caches this function's `db`
    # dependency per-request, so stamping it here reaches the same session
    # object the endpoint itself uses. See app.db.session.set_tenant_context
    # for why this is `SET LOCAL` (transaction-scoped), not `SET`.
    await set_tenant_context(db, tenant_id)

    return CurrentUser(
        id=user_id,
        tenant_id=tenant_id,
        role=Role(payload["role"]),
    )


async def get_tenant_db(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AsyncSession:
    """Yields a DB session scoped to the caller's tenant.

    Enforcing tenant isolation happens at the query layer (section 3): every
    repository/service method filters by tenant_id from CurrentUser, never
    from client-supplied input.

    Phase 0 (KLAROS_PHASE_0_IMPLEMENTATION_PLAN.md §0.2): also stamps the
    session's Postgres transaction with `app.tenant_id` via `SET LOCAL`
    (see app.db.session.set_tenant_context) so the RLS audit-mode policies
    added in this phase's migrations have a real value to read. This is
    additive, request-scoped plumbing only — it does not change query
    behavior by itself (no policy is enforcing yet), and it is a no-op on
    SQLite (the test suite's engine).
    """
    await set_tenant_context(db, current_user.tenant_id)
    return db


def require_permission(permission: Permission):
    async def _check(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not role_has_permission(current_user.role, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required permission: {permission.value}",
            )
        return current_user

    return _check
