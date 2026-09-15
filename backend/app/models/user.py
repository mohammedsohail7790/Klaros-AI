import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin
from app.models.rbac import Role


class User(TenantScopedMixin, Base):
    __tablename__ = "users"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default=Role.STAFF)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Phase 12: real token revocation. Every issued JWT embeds the
    # token_version it was minted with; `get_current_user` rejects a token
    # whose embedded version doesn't match the user's current value.
    # Logout, and any future "sign out everywhere"/deactivation flow,
    # bumps this — which instantly invalidates every outstanding access
    # and refresh token for that user, something a purely stateless JWT
    # (the pre-Phase-12 design) had no way to do short of rotating the
    # whole app's JWT_SECRET.
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),)


class InviteStatus(StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REVOKED = "REVOKED"


class TeamInvite(TenantScopedMixin, Base):
    """A real, DB-backed pending invitation — not a bare stateless token —
    so a tenant can see, list, and revoke outstanding invites (and so a
    second invite to the same email is rejected instead of silently
    creating a duplicate). The actual bearer credential a recipient holds
    is a separate signed JWT (app/core/security.py::create_invite_token)
    carrying this row's id; the JWT alone is never sufficient without the
    row still being PENDING and unexpired, so revoking here immediately
    invalidates an already-sent link."""

    __tablename__ = "team_invites"

    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=InviteStatus.PENDING)
    invited_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
