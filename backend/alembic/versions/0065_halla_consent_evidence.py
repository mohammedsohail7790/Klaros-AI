"""Halla consent evidence history (`halla_consent_evidence`).

Append-only: one row per distinct Halla event that carried valid `data.consent` (HALLA_KLAROS_INTEGRATION_CONTRACT 3.1). Holds the opaque
Halla lead id, the scopes, granted flag, method, wording_version label and Halla's recorded_at -- no wording text, transcript, medical
content or contact details. The current state is derived in code (services/halla_consent.py); nothing is updated or deleted here, and no
retention period is set (an owner/legal decision). Real row-level security as in 0067 (PostgreSQL only).

Validated on PostgreSQL 16 (full chain 0001->head, downgrade to 0064 and back, RLS exercised as a non-owner role). The unreleased MT/Dropshipping
work in the main tree also uses 0065-0067: whichever branch merges second MUST renumber (tests/test_alembic_single_head.py fails on two heads).

Revision ID: 0065_halla_consent_evidence
Revises: 0064
Create Date: 2026-10-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0065_halla_consent_evidence"
down_revision: Union[str, None] = "0064"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = sa.Uuid(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "halla_consent_evidence",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("halla_event_id", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("halla_lead_id", sa.String(255), nullable=True),
        sa.Column("lead_id", _UUID, nullable=True),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("method", sa.String(32), nullable=False),
        sa.Column("wording_version", sa.String(64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "halla_event_id", name="uq_halla_consent_tenant_event"),
    )
    op.create_index("ix_halla_consent_evidence_tenant_id", "halla_consent_evidence", ["tenant_id"])
    op.create_index("ix_halla_consent_evidence_lead_id", "halla_consent_evidence", ["lead_id"])
    op.create_index("ix_halla_consent_tenant_halla_lead", "halla_consent_evidence", ["tenant_id", "halla_lead_id", "recorded_at"])
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE halla_consent_evidence ENABLE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenant_select ON halla_consent_evidence FOR SELECT USING (tenant_id = current_tenant_id())")
    op.execute("CREATE POLICY tenant_insert ON halla_consent_evidence FOR INSERT WITH CHECK (tenant_id = current_tenant_id())")
    op.execute("CREATE POLICY tenant_update ON halla_consent_evidence FOR UPDATE USING (tenant_id = current_tenant_id()) WITH CHECK (tenant_id = current_tenant_id())")
    # deliberately NO delete policy: the history is append-only; erasure, if a policy requires it, is a separate approved operation.


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for policy in ("tenant_select", "tenant_insert", "tenant_update"):
            op.execute(f"DROP POLICY IF EXISTS {policy} ON halla_consent_evidence")
    op.drop_index("ix_halla_consent_tenant_halla_lead", table_name="halla_consent_evidence")
    op.drop_index("ix_halla_consent_evidence_lead_id", table_name="halla_consent_evidence")
    op.drop_index("ix_halla_consent_evidence_tenant_id", table_name="halla_consent_evidence")
    op.drop_table("halla_consent_evidence")
