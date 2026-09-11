"""The reviews/referral-conversion -> marketing content proof loop.

Adds the ONE new piece of state this safety model genuinely requires:
customer_feedback.consent_to_use_publicly (nullable bool; None = never
asked, False = declined, True = explicitly granted). Every other stage
of the required state machine (received/eligibility-review/ready-for-
approval/approved/published/rejected) is either already real, persisted
state on an existing model (CustomerFeedback itself = received;
MarketingContent's own ContentStatus = ready-for-approval through
published/archived, reused unchanged rather than duplicated) or a
deterministic, on-demand computation (eligibility = rating >= a
configurable threshold) that doesn't need its own stored state.

Also adds marketing_content.source_feedback_id so content created from a
review is traceable back to the exact CustomerFeedback row it came from
-- mirrors source_job_id's existing role on the same table exactly.

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "customer_feedback",
        sa.Column("consent_to_use_publicly", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "marketing_content",
        sa.Column("source_feedback_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_marketing_content_source_feedback_id", "marketing_content", ["source_feedback_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_marketing_content_source_feedback_id", table_name="marketing_content")
    op.drop_column("marketing_content", "source_feedback_id")
    op.drop_column("customer_feedback", "consent_to_use_publicly")
