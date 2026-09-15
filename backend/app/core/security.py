from datetime import datetime, timedelta, timezone
from uuid import UUID

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: UUID, tenant_id: UUID, role: str, token_version: int = 0) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "role": role,
        "type": "access",
        "ver": token_version,
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(user_id: UUID, tenant_id: UUID, token_version: int = 0) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "type": "refresh",
        "ver": token_version,
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


class TokenError(Exception):
    pass


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError as exc:
        raise TokenError(str(exc)) from exc


def create_oauth_state_token(tenant_id: UUID, provider: str, user_id: UUID) -> str:
    """Phase 13: a short-lived, signed CSRF/tenant-binding token for a
    provider OAuth redirect flow (QuickBooks, ...). Reuses this project's
    existing JWT_SECRET/signing mechanism rather than inventing a second
    one — the provider's own redirect (`GET .../callback?state=...`) is
    never authenticated by our own JWT (Intuit doesn't carry it), so this
    signed `state` value is the ONLY thing binding an inbound callback
    back to the tenant/user who started the flow, and the only defense
    against a forged/replayed callback."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=10)
    payload = {
        "tenant_id": str(tenant_id),
        "provider": provider,
        "sub": str(user_id),
        "type": "oauth_state",
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_oauth_state_token(token: str, *, expected_provider: str) -> dict:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError as exc:
        raise TokenError(str(exc)) from exc
    if payload.get("type") != "oauth_state" or payload.get("provider") != expected_provider:
        raise TokenError("state token is not a valid oauth_state token for this provider")
    return payload


def create_invite_token(invite_id: UUID, tenant_id: UUID, email: str, role: str) -> str:
    """A team-invite link's bearer credential — same reasoning and
    mechanism as `create_oauth_state_token`/`create_quote_view_token`
    (reuses `JWT_SECRET`, no second credential system). The token alone
    is never sufficient: `TeamInvite.status` still has to be PENDING and
    unexpired at accept time (app/services/team_service.py), so revoking
    the DB row immediately invalidates an already-sent link even though
    the JWT itself would still verify."""
    expire = datetime.now(timezone.utc) + timedelta(days=7)
    payload = {
        "invite_id": str(invite_id),
        "tenant_id": str(tenant_id),
        "email": email,
        "role": role,
        "type": "team_invite",
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_invite_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError as exc:
        raise TokenError(str(exc)) from exc
    if payload.get("type") != "team_invite":
        raise TokenError("token is not a valid team_invite token")
    return payload


def create_quote_view_token(quote_id: UUID, tenant_id: UUID) -> str:
    """Phase 14: a signed, tenant-bound token that lets a CUSTOMER view and
    accept/decline a quote with no Klaros login — Klaros' first
    customer-facing surface with no account behind it. Same reasoning and
    mechanism as `create_oauth_state_token` (reuses `JWT_SECRET`, no
    second credential system), but long-lived (90 days, well past any
    realistic `Quote.valid_until`) since it's mailed/texted to a customer
    once and must keep working when they open it later, not a
    same-session redirect round-trip."""
    expire = datetime.now(timezone.utc) + timedelta(days=90)
    payload = {
        "quote_id": str(quote_id),
        "tenant_id": str(tenant_id),
        "type": "quote_view",
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_quote_view_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError as exc:
        raise TokenError(str(exc)) from exc
    if payload.get("type") != "quote_view":
        raise TokenError("token is not a valid quote_view token")
    return payload


def create_contract_view_token(contract_id: UUID, tenant_id: UUID) -> str:
    """Same reasoning and mechanism as `create_quote_view_token` — the
    contract's own customer-facing view/sign flow has no Klaros login
    behind it either."""
    expire = datetime.now(timezone.utc) + timedelta(days=90)
    payload = {
        "contract_id": str(contract_id),
        "tenant_id": str(tenant_id),
        "type": "contract_view",
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_contract_view_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError as exc:
        raise TokenError(str(exc)) from exc
    if payload.get("type") != "contract_view":
        raise TokenError("token is not a valid contract_view token")
    return payload
