"""App Builder persistence (app/builder).

A project is a spec plus a series of immutable versions; each version owns its full set of files. Files are stored as text
rows (not on local disk) because the only durable object store wired in this deployment is a local filesystem on an
ephemeral host; the builder enforces hard size limits (app/builder/workspace.py) so a project stays a few hundred KB. All
rows are tenant-scoped and, on PostgreSQL, protected by row-level security (migration 0071).
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin

_JSON = JSON().with_variant(JSONB(), "postgresql")


class BuilderProject(TenantScopedMixin, Base):
    __tablename__ = "builder_projects"
    __table_args__ = (UniqueConstraint("tenant_id", "slug", name="uq_builder_projects_tenant_slug"),)

    name: Mapped[str] = mapped_column(String(80), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="new")  # new | working | ready | failed
    spec: Mapped[dict | None] = mapped_column(_JSON, nullable=True)
    initial_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    published_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_paths: Mapped[list] = mapped_column(_JSON, nullable=False, default=list)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class BuilderVersion(TenantScopedMixin, Base):
    __tablename__ = "builder_versions"
    __table_args__ = (UniqueConstraint("project_id", "number", name="uq_builder_versions_project_number"),)

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("builder_projects.id"), nullable=False, index=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # create | edit | file_edit | rollback
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    spec: Mapped[dict] = mapped_column(_JSON, nullable=False)
    manifest: Mapped[list] = mapped_column(_JSON, nullable=False, default=list)
    validation: Mapped[dict] = mapped_column(_JSON, nullable=False, default=dict)
    changes: Mapped[dict] = mapped_column(_JSON, nullable=False, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class BuilderFile(TenantScopedMixin, Base):
    __tablename__ = "builder_files"
    __table_args__ = (UniqueConstraint("version_id", "path", name="uq_builder_files_version_path"),)

    version_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("builder_versions.id"), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    path: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sha: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)


class BuilderRun(TenantScopedMixin, Base):
    """One generation, edit or repair attempt. Durable so a refreshed page, a restarted worker or a second device can see where it is."""

    __tablename__ = "builder_runs"
    __table_args__ = (Index("ix_builder_runs_tenant_state", "tenant_id", "state"),)

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("builder_projects.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # create | edit
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source: Mapped[str | None] = mapped_column(String(16), nullable=True)  # ai | deterministic
    progress: Mapped[list] = mapped_column(_JSON, nullable=False, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation: Mapped[dict | None] = mapped_column(_JSON, nullable=True)
    usage: Mapped[dict] = mapped_column(_JSON, nullable=False, default=dict)
    repair_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    result_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BuilderMessage(TenantScopedMixin, Base):
    __tablename__ = "builder_messages"

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("builder_projects.id"), nullable=False, index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    role: Mapped[str] = mapped_column(String(12), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
