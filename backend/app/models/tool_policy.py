"""Phase 10A: persistent, per-tenant tool policy overrides.

`DEFAULT_TOOL_POLICIES` (app/tools/policy.py) remains the system default and
the seed for every tool not yet overridden — this table only ever *narrows
or widens within what the system allows*, it does not replace the default
table. `PolicyService` (app/services/policy_service.py) is the single place
that resolves a tool's effective policy for a tenant; `ToolRegistry` never
reads this table directly.
"""

import uuid

from sqlalchemy import Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class TenantToolPolicy(TenantScopedMixin, Base):
    __tablename__ = "tenant_tool_policies"
    __table_args__ = (UniqueConstraint("tenant_id", "tool_name", name="uq_tenant_tool_policy"),)

    tool_name: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    policy: Mapped[str] = mapped_column(String(20), nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    configured_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
