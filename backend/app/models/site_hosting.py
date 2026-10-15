"""Sites a business uploads itself (a finished HTML site or a zip), and the custom domains that point at its site.

`UploadedSite` / `UploadedSiteFile` are tenant-scoped like everything else (row-level security on PostgreSQL). `TenantDomain` is deliberately NOT: a visitor's request arrives with only a
hostname, so the mapping hostname -> tenant must be readable before any tenant is known. It holds nothing secret (the hostname is public by definition; the verification token only proves
control of the DNS record and grants no access).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin

UPLOAD_DRAFT, UPLOAD_PUBLISHED, UPLOAD_ARCHIVED = "DRAFT", "PUBLISHED", "ARCHIVED"
DOMAIN_PENDING, DOMAIN_VERIFIED = "PENDING", "VERIFIED"


class UploadedSite(TenantScopedMixin, Base):
    __tablename__ = "uploaded_sites"

    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=UPLOAD_DRAFT)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UploadedSiteFile(TenantScopedMixin, Base):
    __tablename__ = "uploaded_site_files"

    site_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("uploaded_sites.id"), nullable=False, index=True)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class TenantDomain(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tenant_domains"

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    hostname: Mapped[str] = mapped_column(String(253), nullable=False, unique=True)
    verification_token: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=DOMAIN_PENDING)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Whether the frontend host has the domain and is serving it: NOT_AUTOMATED (no host credentials configured), REGISTERING, WAITING_FOR_DNS, LIVE or ERROR.
    edge_status: Mapped[str] = mapped_column(String(20), nullable=False, default="NOT_AUTOMATED")
    edge_detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
