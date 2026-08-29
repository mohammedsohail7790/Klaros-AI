"""Phase 8B: the AI Owner Morning Brief.

A `MorningBrief` is a persisted snapshot — generated_at, insights,
recommendations — not a live query. It is built by calling the same
read-only `insights.*` tools (app/tools/builtin/insight_tools.py) a human
could call directly, through `AIExecutionService`, so every number in it is
traceable to a real tool call and a real audit row (actor_type=AI when
generated automatically by the scheduler, actor_type=USER when a human
clicks "Generate now").

`mode` is never allowed to lie: DETERMINISTIC means the insight/recommendation
text was assembled by rule-based Python from real tool output; AI means a
configured LLM provider (see app/services/ai_provider.py) actually phrased
it. No LLM credentials are configured in this environment (see
PROJECT_STATUS.md), so `mode` is always DETERMINISTIC here — this is
enforced in code, not just documentation.
"""

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Date, DateTime, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class MorningBriefMode(StrEnum):
    DETERMINISTIC = "DETERMINISTIC"
    AI = "AI"


class InsightCategory(StrEnum):
    FINANCE = "FINANCE"
    OPERATIONS = "OPERATIONS"
    SALES = "SALES"
    MARKETING = "MARKETING"
    RETENTION = "RETENTION"
    REFERRAL = "REFERRAL"
    EXCEPTIONS = "EXCEPTIONS"


class InsightPriority(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RecommendationStatus(StrEnum):
    PENDING = "PENDING"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"  # Phase 9: Execute hit an APPROVAL_REQUIRED tool
    EXECUTED = "EXECUTED"
    DISMISSED = "DISMISSED"


class MorningBrief(TenantScopedMixin, Base):
    __tablename__ = "morning_briefs"

    brief_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default=MorningBriefMode.DETERMINISTIC)
    generated_by: Mapped[str] = mapped_column(String(20), nullable=False)  # actor_type: USER | AI | SYSTEM
    headline: Mapped[str] = mapped_column(String(500), nullable=False)
    # Raw structured snapshot of every insight tool's output this brief was
    # built from — so the UI (or a human auditing a claim) can see exactly
    # what real data backed it, not just the synthesized prose.
    source_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    # Phase 9: which real provider (if any) actually produced mode=AI prose,
    # and how long it took — never a secret, just attribution. Both stay
    # NULL for a DETERMINISTIC brief.
    ai_provider: Mapped[str | None] = mapped_column(String(30), nullable=True)
    ai_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ai_generation_ms: Mapped[int | None] = mapped_column(nullable=True)


class MorningBriefInsight(TenantScopedMixin, Base):
    __tablename__ = "morning_brief_insights"

    brief_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default=InsightPriority.LOW)
    summary: Mapped[str] = mapped_column(String(1000), nullable=False)
    related_entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    related_entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    source_tool: Mapped[str | None] = mapped_column(String(100), nullable=True)


class MorningBriefRecommendation(TenantScopedMixin, Base):
    __tablename__ = "morning_brief_recommendations"

    brief_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    what: Mapped[str] = mapped_column(String(500), nullable=False)
    why: Mapped[str] = mapped_column(String(1000), nullable=False)
    related_entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    related_entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    next_action: Mapped[str] = mapped_column(String(500), nullable=False)
    # If set, "Execute" on this recommendation calls exactly this tool with
    # this input through the normal ToolRegistry pipeline (permission/policy/
    # audit) — never a bespoke bypass. If None, the recommendation is
    # informational only and the UI offers "View" (open the related entity),
    # not "Execute".
    executable_tool: Mapped[str | None] = mapped_column(String(100), nullable=True)
    executable_input: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=RecommendationStatus.PENDING)
    # Phase 9: set when Execute hit an APPROVAL_REQUIRED tool — lets the UI
    # link straight to /approvals/{id} instead of just saying "pending".
    approval_request_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
