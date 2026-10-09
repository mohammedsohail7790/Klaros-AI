"""Append-only history of the consent evidence Halla sent (HALLA_KLAROS_INTEGRATION_CONTRACT 3.1).

One row per distinct Halla event that carried valid `data.consent`. Rows are never updated (except to link `lead_id` once the Klaros lead
exists) and never deleted by Klaros: a withdrawal or a re-grant is a NEW row, and the current state is derived (see services/halla_consent.py).
It holds no wording text, transcript, medical content or contact details -- only the opaque Halla lead id, the scopes and the evidence metadata.
No retention period is defined here: that is an owner/legal decision, not a code default.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class HallaConsentEvidence(TenantScopedMixin, Base):
    __tablename__ = "halla_consent_evidence"
    __table_args__ = (
        UniqueConstraint("tenant_id", "halla_event_id", name="uq_halla_consent_tenant_event"),
        Index("ix_halla_consent_tenant_halla_lead", "tenant_id", "halla_lead_id", "recorded_at"),
    )

    halla_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    halla_lead_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    scopes: Mapped[list] = mapped_column(JSON, nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    wording_version: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
