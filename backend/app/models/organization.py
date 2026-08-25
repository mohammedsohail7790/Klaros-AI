from enum import StrEnum

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AutonomyLevel(StrEnum):
    LEVEL_0 = "LEVEL_0"  # AI only recommends
    LEVEL_1 = "LEVEL_1"  # AI can perform low-risk actions
    LEVEL_2 = "LEVEL_2"  # AI can perform configured actions automatically
    LEVEL_3 = "LEVEL_3"  # AI can execute multi-step workflows within policy
    LEVEL_4 = "LEVEL_4"  # highly autonomous, owner handles exceptions


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    plan: Mapped[str] = mapped_column(String(50), nullable=False, default="starter")
    autonomy_level: Mapped[str] = mapped_column(
        String(20), nullable=False, default=AutonomyLevel.LEVEL_0
    )
    billing_status: Mapped[str] = mapped_column(String(50), nullable=False, default="trialing")
