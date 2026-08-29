"""customer lifecycle profiles, retention opportunities, service reminders,
review requests, customer feedback, referral programs/codes/referrals/
rewards, customer risk signals, advocate candidates, retention campaigns/
enrollments/activities

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-26

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamp_cols():
    return [
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "customer_lifecycle_profiles",
        *_timestamp_cols(),
        sa.Column("customer_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("lifecycle_state", sa.String(20), nullable=False, server_default="NEW"),
        sa.Column("jobs_completed_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("first_service_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_service_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state_reason", sa.String(500), nullable=True),
        sa.UniqueConstraint("tenant_id", "customer_id", name="uq_lifecycle_profiles_tenant_customer"),
    )
    op.create_index("ix_customer_lifecycle_profiles_tenant_id", "customer_lifecycle_profiles", ["tenant_id"])
    op.create_index("ix_customer_lifecycle_profiles_customer_id", "customer_lifecycle_profiles", ["customer_id"])
    op.create_index("ix_customer_lifecycle_profiles_lifecycle_state", "customer_lifecycle_profiles", ["lifecycle_state"])

    op.create_table(
        "retention_opportunities",
        *_timestamp_cols(),
        sa.Column("customer_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("priority", sa.String(10), nullable=False, server_default="MEDIUM"),
        sa.Column("status", sa.String(20), nullable=False, server_default="OPEN"),
        sa.Column("source_event", sa.String(100), nullable=True),
        sa.Column("recommended_action", sa.String(500), nullable=True),
        sa.UniqueConstraint(
            "tenant_id", "customer_id", "type", "status", name="uq_retention_opportunities_tenant_customer_type_open"
        ),
    )
    op.create_index("ix_retention_opportunities_tenant_id", "retention_opportunities", ["tenant_id"])
    op.create_index("ix_retention_opportunities_customer_id", "retention_opportunities", ["customer_id"])
    op.create_index("ix_retention_opportunities_type", "retention_opportunities", ["type"])
    op.create_index("ix_retention_opportunities_status", "retention_opportunities", ["status"])

    op.create_table(
        "service_reminders",
        *_timestamp_cols(),
        sa.Column("customer_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_job_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("service_type", sa.String(255), nullable=True),
        sa.Column("reminder_date", sa.Date, nullable=False),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="SCHEDULED"),
    )
    op.create_index("ix_service_reminders_tenant_id", "service_reminders", ["tenant_id"])
    op.create_index("ix_service_reminders_customer_id", "service_reminders", ["customer_id"])
    op.create_index("ix_service_reminders_reminder_date", "service_reminders", ["reminder_date"])

    op.create_table(
        "review_requests",
        *_timestamp_cols(),
        sa.Column("customer_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("job_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("channel", sa.String(10), nullable=False, server_default="INTERNAL"),
        sa.Column("status", sa.String(20), nullable=False, server_default="ELIGIBLE"),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "job_id", name="uq_review_requests_tenant_job"),
    )
    op.create_index("ix_review_requests_tenant_id", "review_requests", ["tenant_id"])
    op.create_index("ix_review_requests_customer_id", "review_requests", ["customer_id"])
    op.create_index("ix_review_requests_job_id", "review_requests", ["job_id"])

    op.create_table(
        "customer_feedback",
        *_timestamp_cols(),
        sa.Column("customer_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("job_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("rating", sa.Integer, nullable=True),
        sa.Column("sentiment", sa.String(10), nullable=True),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("source", sa.String(50), nullable=False, server_default="manual"),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_customer_feedback_tenant_id", "customer_feedback", ["tenant_id"])
    op.create_index("ix_customer_feedback_customer_id", "customer_feedback", ["customer_id"])

    op.create_table(
        "referral_programs",
        *_timestamp_cols(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("campaign_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("reward_type", sa.String(50), nullable=False, server_default="credit"),
        sa.Column("reward_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
    )
    op.create_index("ix_referral_programs_tenant_id", "referral_programs", ["tenant_id"])
    op.create_index("ix_referral_programs_campaign_id", "referral_programs", ["campaign_id"])

    op.create_table(
        "referral_codes",
        *_timestamp_cols(),
        sa.Column("program_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("customer_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.UniqueConstraint("tenant_id", "code", name="uq_referral_codes_tenant_code"),
        sa.UniqueConstraint("tenant_id", "program_id", "customer_id", name="uq_referral_codes_tenant_program_customer"),
    )
    op.create_index("ix_referral_codes_tenant_id", "referral_codes", ["tenant_id"])
    op.create_index("ix_referral_codes_program_id", "referral_codes", ["program_id"])
    op.create_index("ix_referral_codes_customer_id", "referral_codes", ["customer_id"])

    op.create_table(
        "referrals",
        *_timestamp_cols(),
        sa.Column("program_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("referral_code_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("referrer_customer_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("lead_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("referred_customer_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("job_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("invoice_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="CREATED"),
        sa.Column("revenue_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("collected_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("created_at_referral", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "lead_id", name="uq_referrals_tenant_lead"),
    )
    op.create_index("ix_referrals_tenant_id", "referrals", ["tenant_id"])
    op.create_index("ix_referrals_program_id", "referrals", ["program_id"])
    op.create_index("ix_referrals_referral_code_id", "referrals", ["referral_code_id"])
    op.create_index("ix_referrals_referrer_customer_id", "referrals", ["referrer_customer_id"])
    op.create_index("ix_referrals_lead_id", "referrals", ["lead_id"])
    op.create_index("ix_referrals_status", "referrals", ["status"])

    op.create_table(
        "referral_rewards",
        *_timestamp_cols(),
        sa.Column("referral_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("customer_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("requested_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("approved_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "referral_id", name="uq_referral_rewards_tenant_referral"),
    )
    op.create_index("ix_referral_rewards_tenant_id", "referral_rewards", ["tenant_id"])
    op.create_index("ix_referral_rewards_referral_id", "referral_rewards", ["referral_id"])
    op.create_index("ix_referral_rewards_customer_id", "referral_rewards", ["customer_id"])

    op.create_table(
        "customer_risk_signals",
        *_timestamp_cols(),
        sa.Column("customer_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("signal_type", sa.String(30), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False, server_default="LOW"),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.UniqueConstraint(
            "tenant_id", "customer_id", "signal_type", "resolved", name="uq_customer_risk_signals_tenant_customer_type_open"
        ),
    )
    op.create_index("ix_customer_risk_signals_tenant_id", "customer_risk_signals", ["tenant_id"])
    op.create_index("ix_customer_risk_signals_customer_id", "customer_risk_signals", ["customer_id"])
    op.create_index("ix_customer_risk_signals_signal_type", "customer_risk_signals", ["signal_type"])

    op.create_table(
        "advocate_candidates",
        *_timestamp_cols(),
        sa.Column("customer_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("signals", sa.Text, nullable=False),
        sa.Column("priority", sa.String(10), nullable=False, server_default="MEDIUM"),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("identified_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "customer_id", "status", name="uq_advocate_candidates_tenant_customer_pending"),
    )
    op.create_index("ix_advocate_candidates_tenant_id", "advocate_candidates", ["tenant_id"])
    op.create_index("ix_advocate_candidates_customer_id", "advocate_candidates", ["customer_id"])

    op.create_table(
        "retention_campaigns",
        *_timestamp_cols(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
    )
    op.create_index("ix_retention_campaigns_tenant_id", "retention_campaigns", ["tenant_id"])

    op.create_table(
        "retention_enrollments",
        *_timestamp_cols(),
        sa.Column("campaign_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("customer_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "campaign_id", "customer_id", name="uq_retention_enroll_tenant_campaign_customer"),
    )
    op.create_index("ix_retention_enrollments_tenant_id", "retention_enrollments", ["tenant_id"])
    op.create_index("ix_retention_enrollments_campaign_id", "retention_enrollments", ["campaign_id"])
    op.create_index("ix_retention_enrollments_customer_id", "retention_enrollments", ["customer_id"])

    op.create_table(
        "retention_activities",
        *_timestamp_cols(),
        sa.Column("enrollment_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("channel", sa.String(10), nullable=False, server_default="EMAIL"),
        sa.Column("template", sa.String(50), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_retention_activities_tenant_id", "retention_activities", ["tenant_id"])
    op.create_index("ix_retention_activities_enrollment_id", "retention_activities", ["enrollment_id"])
    op.create_index("ix_retention_activities_scheduled_for", "retention_activities", ["scheduled_for"])


def downgrade() -> None:
    op.drop_table("retention_activities")
    op.drop_table("retention_enrollments")
    op.drop_table("retention_campaigns")
    op.drop_table("advocate_candidates")
    op.drop_table("customer_risk_signals")
    op.drop_table("referral_rewards")
    op.drop_table("referrals")
    op.drop_table("referral_codes")
    op.drop_table("referral_programs")
    op.drop_table("customer_feedback")
    op.drop_table("review_requests")
    op.drop_table("service_reminders")
    op.drop_table("retention_opportunities")
    op.drop_table("customer_lifecycle_profiles")
