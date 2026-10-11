"""Account-lifecycle tables that must be readable BEFORE a tenant is known, so they carry no row-level security:

  AccountDirectory     email -> workspace(s). Lets a person sign in (or ask for a password reset) with just an email address instead of having to know a
                       "company slug". Holds only the email, the tenant id and the workspace slug: no names, no business data.
  ContactMessage       a message from the public contact form (no tenant: the sender is not signed in).
  PasswordResetToken   a single-use, expiring password-reset credential. Only the SHA-256 of the secret is stored, never the secret itself.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class AccountDirectory(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "account_directory"
    __table_args__ = (UniqueConstraint("email", "tenant_id", name="uq_account_directory_email_tenant"),)

    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    organization_slug: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PasswordResetToken(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "password_reset_tokens"
    __table_args__ = (Index("ix_password_reset_tokens_user", "user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ContactMessage(UUIDPrimaryKeyMixin, Base):
    """A message sent from the public contact form. No tenant: the sender is not signed in."""

    __tablename__ = "contact_messages"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
