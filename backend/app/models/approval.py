import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ApprovalRequest(TenantScopedMixin, Base):
    """section 10 / section 8: the persisted half of the approval boundary.

    Created whenever a tool execution resolves to APPROVAL_REQUIRED instead of
    running. Nothing downstream executes until a human moves this to APPROVED.
    """

    __tablename__ = "approval_requests"

    requested_by_type: Mapped[str] = mapped_column(String(20), nullable=False)  # ActorType
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    action_type: Mapped[str] = mapped_column(String(100), nullable=False)
    reason: Mapped[str] = mapped_column(String(2000), nullable=False)
    tool_input: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ApprovalStatus.PENDING)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
