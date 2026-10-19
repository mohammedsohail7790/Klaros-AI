"""WhatsApp messages that come back to the business through Klaros (what a customer wrote to the business's WhatsApp number).

One row per message, tenant-scoped (row-level security on PostgreSQL). The row also IS the record of the customer's 24-hour window: WhatsApp only allows a free-form reply within 24 hours of the
customer's last message, so the newest `received_at` for a phone number decides whether a text or an approved template may be sent. `message_id` is Meta's own id and is unique per business, so a
redelivered webhook is stored once. Outbound messages are recorded in `communication_logs` (channel WHATSAPP), and Meta's delivery receipts update that row.
"""

from datetime import datetime
import uuid

from sqlalchemy import Boolean, DateTime, Index, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class WhatsAppInboundMessage(TenantScopedMixin, Base):
    __tablename__ = "whatsapp_inbound_messages"
    __table_args__ = (
        UniqueConstraint("tenant_id", "message_id", name="uq_whatsapp_inbound_message"),
        Index("ix_whatsapp_inbound_phone", "tenant_id", "phone_normalized"),
    )

    message_id: Mapped[str] = mapped_column(String(255), nullable=False)  # Meta's wamid
    wa_id: Mapped[str] = mapped_column(String(32), nullable=False)  # the sender's WhatsApp id (their number, digits)
    phone_normalized: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # lead | customer | None when nobody matches
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    message_type: Mapped[str] = mapped_column(String(30), nullable=False, default="text")
    profile_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_stored: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)  # False when consent does not cover keeping it, or the sender is unknown
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
