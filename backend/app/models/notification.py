from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class Notification(TenantScopedMixin, Base):
    __tablename__ = "notifications"

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(String(2000), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="INFO")
    category: Mapped[str] = mapped_column(String(100), nullable=False, default="general")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
