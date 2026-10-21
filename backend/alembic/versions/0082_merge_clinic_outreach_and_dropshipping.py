"""Merge the clinic-outreach and dropshipping migration chains.

Both branched from 0079_catalog_real_connectors (clinic outreach as 0080_clinic_outreach, the dropshipping store as 0080_dropshipping_store -> 0081_dropshipping_order_notes), so
`alembic upgrade head` found two heads and refused to run. This revision joins them and changes nothing in the schema. A database that already has the dropshipping chain
(production) applies 0080_clinic_outreach and then this; a fresh database applies both chains in turn.

Rollback: `alembic downgrade 0081_dropshipping_order_notes` (or `0080_clinic_outreach`) removes only this marker.

Revision ID: 0082_merge_outreach_dropshipping
Revises: 0080_clinic_outreach, 0081_dropshipping_order_notes
Create Date: 2026-10-21
"""
from typing import Sequence, Union

revision: str = "0082_merge_outreach_dropshipping"
down_revision: Union[str, Sequence[str], None] = ("0080_clinic_outreach", "0081_dropshipping_order_notes")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
