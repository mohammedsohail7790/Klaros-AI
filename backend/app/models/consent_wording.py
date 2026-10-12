"""The exact consent wording a business shows to the public, approved by its owner.

Append-only and immutable: a wording is never edited. Changing the text means approving a NEW version, so every piece of consent evidence (`wording_version` on a
lead's consent record) always points at the exact words the person agreed to. Rows are inserted by the owner's explicit approval (services/consent_wording_service.py)
and never updated or deleted by Klaros.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class ConsentWording(TenantScopedMixin, Base):
    __tablename__ = "consent_wordings"
    __table_args__ = (UniqueConstraint("tenant_id", "version", name="uq_consent_wordings_tenant_version"),)

    version: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. WEB-v1; the label stored on consent evidence
    text: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="en", server_default="en")
    approved_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
