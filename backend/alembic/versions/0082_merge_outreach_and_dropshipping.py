"""Merge the clinic outreach and dropshipping migration branches into one head.

Both 0080_clinic_outreach and 0080_dropshipping_store were written against 0079_catalog_real_connectors, so main had two heads and `alembic upgrade head` (the container start command and CI) refused to run. This revision changes no schema; it only joins the two.

Revision ID: 0082_merge_outreach_and_dropshipping
Revises: 0080_clinic_outreach, 0081_dropshipping_order_notes
Create Date: 2026-10-21
"""
from typing import Sequence, Union

revision: str = "0082_merge_outreach_and_dropshipping"
down_revision: Union[str, Sequence[str], None] = ("0080_clinic_outreach", "0081_dropshipping_order_notes")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
