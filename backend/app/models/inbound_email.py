"""Email replies that come back to the business through Klaros.

`EmailReplyRoute` maps the hidden reply address of one patient or clinic (`r-<token>@<reply domain>`) to the business and the record it belongs to. It is NOT tenant-scoped on purpose: a reply
arrives knowing only its token, so the lookup must work before any business is known. The token is unguessable and is only ever sent to the person it was made for. `InboundEmail` holds the
replies themselves and IS tenant-scoped (row-level security on PostgreSQL).
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class EmailReplyRoute(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "email_reply_routes"
    __table_args__ = (UniqueConstraint("tenant_id", "entity_type", "entity_id", name="uq_email_reply_route_entity"),)

    token: Mapped[str] = mapped_column(String(24), nullable=False, unique=True, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)  # lead | provider
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)


class InboundEmail(TenantScopedMixin, Base):
    __tablename__ = "inbound_emails"
    __table_args__ = (UniqueConstraint("tenant_id", "message_id", name="uq_inbound_email_message"),)

    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    message_id: Mapped[str] = mapped_column(String(500), nullable=False)
    from_address: Mapped[str] = mapped_column(String(320), nullable=False)
    from_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_stored: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)  # False when the person's consent does not cover keeping what they wrote
    sender_matches: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)  # the reply came from the address we wrote to
    spam_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    attachment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
