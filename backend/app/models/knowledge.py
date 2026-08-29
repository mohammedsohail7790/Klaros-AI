"""Phase 12: the Company-OS Knowledge Layer — the file-based source of
truth an Owner (or, going forward, the AI) can read/edit for how their
specific business actually operates: pricing rules, qualification
criteria, brand voice, compliance limits, and so on.

Deliberately DB-backed, not literal files on a local disk: this codebase
runs as a stateless container (or several, behind a load balancer) in
production, and Postgres is already the single source of truth for every
other tenant-scoped record — a local-filesystem "knowledge layer" would
either not survive a redeploy or require its own replicated storage layer
for no real benefit. The `path` convention (`office/pricing-rules.md`,
`brand/voice-guide.md`, ...) is preserved because it's how a human (or the
AI) addresses a document; the storage underneath is a normal table.
"""

import uuid

from sqlalchemy import String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class KnowledgeFile(TenantScopedMixin, Base):
    __tablename__ = "knowledge_files"
    __table_args__ = (UniqueConstraint("tenant_id", "path", name="uq_knowledge_file_path"),)

    # e.g. "office/pricing-rules.md" — category/filename, markdown content.
    path: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
