from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class CommunicationLog(TenantScopedMixin, Base):
    """section 16: every message sent — real or internal-test — is logged here."""

    __tablename__ = "communication_logs"

    channel: Mapped[str] = mapped_column(String(20), nullable=False)  # EMAIL | SMS
    template: Mapped[str] = mapped_column(String(100), nullable=False)
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)  # SENT | FAILED | SENT_NO_EMAIL_ON_FILE
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    # Phase 12C: the real provider's own message id (Twilio MessageSid,
    # SendGrid message id) — lets a later delivery-status webhook find and
    # update this exact row. Null for internal-test sends, which have no
    # real provider-side id.
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
