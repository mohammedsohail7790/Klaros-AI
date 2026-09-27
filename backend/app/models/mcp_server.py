"""Phase 9: the Klaros MCP **server** direction only (KLAROS_FINAL_AGENT_
MODEL.md's explicit MCP-server carve-out; the MCP-client direction remains
rejected per ADR-002/KLAROS_DO_NOT_BUILD_YET.md and is NOT implemented
anywhere in this module or its service layer).

Two tenant-owned tables, both deliberately small:

  - `McpToolExposure` — the exposure allowlist. The single source of truth
    for "which already-governed `ToolRegistry` tools may an external MCP
    client invoke for this tenant." Never auto-populated from the tool
    registry; a row only exists because an OWNER/ADMIN explicitly created
    it (see app/api/v1/mcp_admin.py, gated on `Permission.MANAGE_MCP_SERVER`).
    `enabled=False` rows are kept (not deleted) so there is an audit trail
    of what was once exposed and was later withdrawn.

  - `McpClientCredential` — a scoped bearer credential for one external MCP
    client, tied to exactly one tenant and one Klaros `Role` (reusing the
    existing `Role`/RBAC system unchanged — an MCP client is authorized
    exactly like a human user of that role would be, never more). The raw
    token is shown to the admin exactly once at creation time and never
    stored — only `token_hash` (SHA-256 of the raw token) persists, same
    "never store the secret itself" discipline as
    app/integrations/credential_store.py, but a one-way hash rather than
    reversible encryption because nothing ever needs to recover the raw
    token after issuance (only verify a presented one, like a password).
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class McpCredentialStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class McpToolExposure(TenantScopedMixin, Base):
    """One row per (tenant, tool_name) exposure decision. `tool_name` must
    match a name registered in `ToolRegistry` at the time it's looked up
    (checked at read time, not enforced by a DB FK — the registry is an
    in-process Python object, not a DB table), but a row can pre-exist for
    a tool name that isn't registered in a given process; the MCP server
    simply skips it (fails closed, never invents access)."""

    __tablename__ = "mcp_tool_exposures"

    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "tool_name", name="uq_mcp_tool_exposures_tenant_tool"),
    )


class McpClientCredential(TenantScopedMixin, Base):
    """One row per issued MCP client credential. `role` reuses the exact
    `Role` enum every human user has — an authenticated MCP client is
    authorized through the SAME `role_has_permission`/`ToolRegistry.execute()`
    pipeline as any other caller of that role, never a parallel one."""

    __tablename__ = "mcp_client_credentials"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(30), nullable=False)  # Role
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)  # sha256 hex digest
    # First 8 chars of the raw token, for display/identification in the
    # admin UI only ("mcp_ab12cd34...") — never enough to reconstruct or
    # brute-force the credential, and never used for the actual auth check
    # (token_hash is, via a full SHA-256 comparison).
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=McpCredentialStatus.ACTIVE)
    created_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
