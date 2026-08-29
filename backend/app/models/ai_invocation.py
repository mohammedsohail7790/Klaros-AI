"""Phase 12E: real audit + usage trail for direct AI-provider calls
(Anthropic/OpenAI Messages/Chat Completions), distinct from `AuditLog`
(which records TOOL executions via ToolRegistry — a raw provider call for
prose/structured-output generation is not itself a tool execution, so it
had no audit trail at all before this).

Never stores the API key, the raw prompt, or the raw response — only safe
metadata (provider/model/operation/timing/token counts/error
classification) and short, non-PII summary strings. See
app/services/ai_invocation_log_service.py for the one place these rows are
written.
"""

import uuid
from typing import Any

from sqlalchemy import JSON, Boolean, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class AIInvocationLog(TenantScopedMixin, Base):
    __tablename__ = "ai_invocation_logs"

    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)  # human | ai | system
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    operation: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g. "lead_qualification_advisory"
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Real cost only when the provider's pricing is known and applied —
    # NULL (not 0) means "usage recorded, cost not computed," never a
    # fabricated number.
    estimated_cost_usd: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Safe, non-PII descriptive metadata only — e.g. {"insight_count": 3},
    # {"lead_id": "..."} (an id, not lead content). Never raw prompt/response text.
    input_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    output_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
