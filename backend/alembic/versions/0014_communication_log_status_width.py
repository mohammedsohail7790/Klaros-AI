"""Phase 12B: communication_logs.status was VARCHAR(20), but real status
values in use (e.g. "SENT_NO_EMAIL_ON_FILE", 21 chars) exceed that width.
SQLite never enforces VARCHAR length at all, so this was completely
invisible until verified against real PostgreSQL, which does enforce it —
`finance.send_invoice`'s internal-test delivery path failed outright with
`StringDataRightTruncationError` on any invoice for a customer with no
email on file. Widened to 40, comfortably covering current and
near-future status values.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("communication_logs") as batch_op:
        batch_op.alter_column("status", type_=sa.String(40), existing_type=sa.String(20))


def downgrade() -> None:
    with op.batch_alter_table("communication_logs") as batch_op:
        batch_op.alter_column("status", type_=sa.String(20), existing_type=sa.String(40))
