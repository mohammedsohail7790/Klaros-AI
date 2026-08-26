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
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # SENT | FAILED
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
