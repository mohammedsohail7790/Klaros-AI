"""Phase 13: Company Memory — the persistent, governed layer for durable
owner preferences/business rules/context that should influence future AI
reasoning, distinct from both:

- KNOWLEDGE (app/models/knowledge.py): source-backed documents the owner
  wrote out in full (pricing policy, brand voice) — retrieved by semantic
  similarity to a query.
- BUSINESS DATA (CRM/Finance/Operations models): ordinary transactional
  records (a specific customer's specific appointment).

Company Memory is neither: it's short, structured, KEYED facts/preferences
("preferred_appointment_time" -> "morning") that AI context-building reads
directly, in full, every time — no embedding, no similarity search (Rule
17: structured retrieval, not vector search).

The owner is always the authority. AI never writes an ACTIVE row directly
— see MemoryService.propose_memory vs. create_memory (app/services/
company_memory_service.py): only a human, through create_memory
(source=OWNER_EXPLICIT/OWNER_CORRECTION) or confirm_memory (any source,
but the CONFIRM action itself is a human API call gated by
MANAGE_MEMORY), can ever set status=ACTIVE.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Index, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class MemoryType(StrEnum):
    OWNER_PREFERENCE = "OWNER_PREFERENCE"
    BUSINESS_RULE = "BUSINESS_RULE"
    OPERATIONAL_PREFERENCE = "OPERATIONAL_PREFERENCE"
    AI_FEEDBACK = "AI_FEEDBACK"
    COMPANY_CONTEXT = "COMPANY_CONTEXT"
    TEMPORAL_CONTEXT = "TEMPORAL_CONTEXT"


class MemorySource(StrEnum):
    """Precedence, highest to lowest authority (see
    app/services/company_memory_service.py::_SOURCE_RANK): a lower-ranked
    source can never silently supersede a higher-ranked one's currently
    ACTIVE value for the same key."""

    OWNER_EXPLICIT = "OWNER_EXPLICIT"
    OWNER_CORRECTION = "OWNER_CORRECTION"
    OWNER_APPROVAL = "OWNER_APPROVAL"
    SYSTEM_DERIVED = "SYSTEM_DERIVED"
    AI_PROPOSED = "AI_PROPOSED"


class MemoryStatus(StrEnum):
    PENDING = "PENDING"  # AI-proposed, awaiting owner confirm/reject — never in AI context
    ACTIVE = "ACTIVE"  # currently in effect — at most one ACTIVE row per (tenant_id, key)
    ARCHIVED = "ARCHIVED"  # automatically superseded by a newer ACTIVE value for the same key
    REVOKED = "REVOKED"  # explicitly revoked by the owner while ACTIVE
    REJECTED = "REJECTED"  # an AI-proposed PENDING candidate explicitly rejected by the owner


# Bounds (Rule 24/25) — a memory is a short structured fact, never a
# dumping ground for arbitrary long-form instructions.
MAX_KEY_LENGTH = 100
MAX_VALUE_LENGTH = 2000
MAX_DESCRIPTION_LENGTH = 2000


class CompanyMemory(TenantScopedMixin, Base):
    __tablename__ = "company_memories"
    __table_args__ = (
        # Real DB-level enforcement (Rule 30) — at most one ACTIVE row per
        # (tenant_id, key), so two genuinely concurrent supersession writes
        # can never both commit an ACTIVE row for the same key; the loser
        # gets a real IntegrityError (see CompanyMemoryService's
        # MemoryConcurrentUpdateError). Declared here too (not just in
        # migration 0034) so `Base.metadata.create_all()` — what the test
        # suite's `_reset_database` fixture uses — also creates it.
        Index(
            "uq_company_memories_one_active_per_key", "tenant_id", "key", unique=True,
            postgresql_where=text("status = 'ACTIVE'"), sqlite_where=text("status = 'ACTIVE'"),
        ),
    )

    memory_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    # Structured identifier, e.g. "preferred_appointment_time" — validated
    # (see company_memory_service.py::validate_key) to a bounded
    # lowercase/underscore/digit shape, never a free-form sentence. Used
    # as the supersession key: only one ACTIVE row may exist per
    # (tenant_id, key) at a time.
    key: Mapped[str] = mapped_column(String(MAX_KEY_LENGTH), nullable=False, index=True)
    value: Mapped[str] = mapped_column(String(MAX_VALUE_LENGTH), nullable=False)
    description: Mapped[str | None] = mapped_column(String(MAX_DESCRIPTION_LENGTH), nullable=True)

    source: Mapped[str] = mapped_column(String(30), nullable=False)
    source_entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=MemoryStatus.ACTIVE, index=True)
    # Mainly meaningful for AI_PROPOSED candidates; NULL for owner-authored
    # entries (a human stating a preference has no "confidence" — it just is).
    confidence: Mapped[float | None] = mapped_column(nullable=True)

    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Self-referential chain (Rule 7): when a new value supersedes an old
    # one for the same key, the new row's `supersedes_id` points at the
    # row it replaced — full history is just "walk this chain", no
    # separate version-log table needed.
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
