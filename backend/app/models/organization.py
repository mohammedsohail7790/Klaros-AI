from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Numeric, String
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
    # Klaros's own SaaS subscription tier — "solo" | "growth" | "scale".
    # See app/services/billing_service.py::PLAN_LIMITS for what each tier
    # actually gates.
    plan: Mapped[str] = mapped_column(String(50), nullable=False, default="starter")
    autonomy_level: Mapped[str] = mapped_column(
        String(20), nullable=False, default=AutonomyLevel.LEVEL_0
    )
    # "trialing" | "active" | "past_due" | "canceled" — kept in sync with
    # Stripe's own subscription status by app/services/billing_service.py's
    # webhook handlers (app/api/v1/billing.py's POST /billing/webhook).
    billing_status: Mapped[str] = mapped_column(String(50), nullable=False, default="trialing")
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Phase 5: MANUAL / INTERNAL TEST DATA only — no bank integration exists.
    # Cash position reports "NOT_CONNECTED" unless this is explicitly set.
    manual_starting_cash: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    # Phase 8: Morning Brief scheduling. The event worker's already-running
    # poll loop checks this once per tick (see
    # app/services/morning_brief_service.py's check_and_generate_scheduled)
    # rather than a second sleep-based scheduler — disabled by default so no
    # tenant gets a brief generated without opting in.
    morning_brief_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    morning_brief_local_time: Mapped[str] = mapped_column(String(5), nullable=False, default="07:00")
    morning_brief_timezone: Mapped[str] = mapped_column(String(50), nullable=False, default="UTC")
    # Phase 11 (Automation Engine SCHEDULE trigger): the tenant's general
    # business timezone, distinct from morning_brief_timezone above (which
    # stays feature-specific and untouched for backward compatibility) —
    # used to resolve a SCHEDULE-triggered automation's local time-of-day
    # (see AutomationService.check_and_dispatch_scheduled). No general
    # tenant timezone field existed before this; this is the smallest
    # correct extension rather than overloading morning_brief_timezone's
    # name for an unrelated purpose.
    timezone: Mapped[str] = mapped_column(String(50), nullable=False, default="UTC")
