"""campaigns, marketing spend, attribution, campaign leads/conversions,
content engine, SEO/local, outbound list building, nurture, reactivation

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-26

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
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
        "campaigns",
        *_timestamp_cols(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("channel", sa.String(30), nullable=False),
        sa.Column("objective", sa.String(30), nullable=False, server_default="LEAD_GEN"),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("start_date", sa.Date, nullable=True),
        sa.Column("end_date", sa.Date, nullable=True),
        sa.Column("monthly_budget", sa.Numeric(14, 2), nullable=True),
        sa.Column("total_budget", sa.Numeric(14, 2), nullable=True),
        sa.Column("external_provider", sa.String(50), nullable=True),
        sa.Column("external_campaign_id", sa.String(255), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
    )
    op.create_index("ix_campaigns_tenant_id", "campaigns", ["tenant_id"])
    op.create_index("ix_campaigns_channel", "campaigns", ["channel"])
    op.create_index("ix_campaigns_status", "campaigns", ["status"])

    op.create_table(
        "marketing_spend",
        *_timestamp_cols(),
        sa.Column("channel", sa.String(30), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("spend_date", sa.Date, nullable=False),
        sa.Column("source", sa.String(50), nullable=False, server_default="manual"),
        sa.Column("external_reference", sa.String(255), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
    )
    op.create_index("ix_marketing_spend_tenant_id", "marketing_spend", ["tenant_id"])
    op.create_index("ix_marketing_spend_channel", "marketing_spend", ["channel"])
    op.create_index("ix_marketing_spend_spend_date", "marketing_spend", ["spend_date"])

    op.create_table(
        "marketing_spend_allocations",
        *_timestamp_cols(),
        sa.Column("spend_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("campaign_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
    )
    op.create_index("ix_marketing_spend_allocations_tenant_id", "marketing_spend_allocations", ["tenant_id"])
    op.create_index("ix_marketing_spend_allocations_spend_id", "marketing_spend_allocations", ["spend_id"])
    op.create_index("ix_marketing_spend_allocations_campaign_id", "marketing_spend_allocations", ["campaign_id"])

    op.create_table(
        "marketing_lead_sources",
        *_timestamp_cols(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("channel", sa.String(30), nullable=False),
        sa.Column("is_paid", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("tenant_id", "name", name="uq_marketing_lead_sources_tenant_name"),
    )
    op.create_index("ix_marketing_lead_sources_tenant_id", "marketing_lead_sources", ["tenant_id"])

    op.create_table(
        "lead_attributions",
        *_timestamp_cols(),
        sa.Column("lead_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("campaign_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("source", sa.String(100), nullable=True),
        sa.Column("medium", sa.String(100), nullable=True),
        sa.Column("landing_page", sa.String(500), nullable=True),
        sa.Column("referral_source", sa.String(255), nullable=True),
        sa.Column("utm_source", sa.String(255), nullable=True),
        sa.Column("utm_medium", sa.String(255), nullable=True),
        sa.Column("utm_campaign", sa.String(255), nullable=True),
        sa.Column("utm_term", sa.String(255), nullable=True),
        sa.Column("utm_content", sa.String(255), nullable=True),
        sa.Column("click_id", sa.String(255), nullable=True),
        sa.Column("first_touch_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_touch_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attribution_model", sa.String(20), nullable=False, server_default="SOURCE_ONLY"),
        sa.UniqueConstraint("tenant_id", "lead_id", name="uq_lead_attributions_tenant_lead"),
    )
    op.create_index("ix_lead_attributions_tenant_id", "lead_attributions", ["tenant_id"])
    op.create_index("ix_lead_attributions_lead_id", "lead_attributions", ["lead_id"])
    op.create_index("ix_lead_attributions_campaign_id", "lead_attributions", ["campaign_id"])

    op.create_table(
        "campaign_leads",
        *_timestamp_cols(),
        sa.Column("campaign_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("lead_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("attributed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "campaign_id", "lead_id", name="uq_campaign_leads_tenant_campaign_lead"),
    )
    op.create_index("ix_campaign_leads_tenant_id", "campaign_leads", ["tenant_id"])
    op.create_index("ix_campaign_leads_campaign_id", "campaign_leads", ["campaign_id"])
    op.create_index("ix_campaign_leads_lead_id", "campaign_leads", ["lead_id"])

    op.create_table(
        "campaign_conversions",
        *_timestamp_cols(),
        sa.Column("campaign_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("lead_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("customer_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("job_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("invoice_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("stage", sa.String(20), nullable=False, server_default="LEAD"),
        sa.Column("revenue_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("collected_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("updated_at_stage", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "campaign_id", "lead_id", name="uq_campaign_conversions_tenant_campaign_lead"
        ),
    )
    op.create_index("ix_campaign_conversions_tenant_id", "campaign_conversions", ["tenant_id"])
    op.create_index("ix_campaign_conversions_campaign_id", "campaign_conversions", ["campaign_id"])
    op.create_index("ix_campaign_conversions_lead_id", "campaign_conversions", ["lead_id"])

    op.create_table(
        "marketing_content",
        *_timestamp_cols(),
        sa.Column("source_job_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="IDEA"),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("ai_generated", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_marketing_content_tenant_id", "marketing_content", ["tenant_id"])
    op.create_index("ix_marketing_content_source_job_id", "marketing_content", ["source_job_id"])
    op.create_index("ix_marketing_content_status", "marketing_content", ["status"])

    op.create_table(
        "content_assets",
        *_timestamp_cols(),
        sa.Column("content_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("job_attachment_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("caption", sa.String(500), nullable=True),
    )
    op.create_index("ix_content_assets_tenant_id", "content_assets", ["tenant_id"])
    op.create_index("ix_content_assets_content_id", "content_assets", ["content_id"])

    op.create_table(
        "content_variants",
        *_timestamp_cols(),
        sa.Column("content_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("body_text", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
    )
    op.create_index("ix_content_variants_tenant_id", "content_variants", ["tenant_id"])
    op.create_index("ix_content_variants_content_id", "content_variants", ["content_id"])

    op.create_table(
        "content_publications",
        *_timestamp_cols(),
        sa.Column("content_variant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider", sa.String(50), nullable=False, server_default="internal_test_publication"),
        sa.Column("external_reference", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="SCHEDULED"),
    )
    op.create_index("ix_content_publications_tenant_id", "content_publications", ["tenant_id"])
    op.create_index("ix_content_publications_content_variant_id", "content_publications", ["content_variant_id"])

    op.create_table(
        "content_performance",
        *_timestamp_cols(),
        sa.Column("content_publication_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("impressions", sa.Integer, nullable=True),
        sa.Column("clicks", sa.Integer, nullable=True),
        sa.Column("likes", sa.Integer, nullable=True),
        sa.Column("shares", sa.Integer, nullable=True),
        sa.Column("comments", sa.Integer, nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(50), nullable=False, server_default="manual"),
    )
    op.create_index("ix_content_performance_tenant_id", "content_performance", ["tenant_id"])
    op.create_index("ix_content_performance_content_publication_id", "content_performance", ["content_publication_id"])

    op.create_table(
        "seo_pages",
        *_timestamp_cols(),
        sa.Column("service", sa.String(255), nullable=False),
        sa.Column("location", sa.String(255), nullable=False),
        sa.Column("url_slug", sa.String(255), nullable=True),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("meta_title", sa.String(255), nullable=True),
        sa.Column("meta_description", sa.String(500), nullable=True),
        sa.Column("h1", sa.String(255), nullable=True),
        sa.Column("body_draft", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("ai_generated", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_seo_pages_tenant_id", "seo_pages", ["tenant_id"])

    op.create_table(
        "seo_keywords",
        *_timestamp_cols(),
        sa.Column("page_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("keyword", sa.String(255), nullable=False),
        sa.Column("target_location", sa.String(255), nullable=True),
        sa.Column("search_volume", sa.Integer, nullable=True),
        sa.Column("current_ranking", sa.Integer, nullable=True),
        sa.Column("ranking_observed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_seo_keywords_tenant_id", "seo_keywords", ["tenant_id"])
    op.create_index("ix_seo_keywords_page_id", "seo_keywords", ["page_id"])

    op.create_table(
        "seo_opportunities",
        *_timestamp_cols(),
        sa.Column("service", sa.String(255), nullable=False),
        sa.Column("location", sa.String(255), nullable=False),
        sa.Column("rationale", sa.Text, nullable=True),
        sa.Column("priority", sa.String(20), nullable=False, server_default="MEDIUM"),
        sa.Column("page_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.create_index("ix_seo_opportunities_tenant_id", "seo_opportunities", ["tenant_id"])

    op.create_table(
        "local_listings",
        *_timestamp_cols(),
        sa.Column("business_name", sa.String(255), nullable=False),
        sa.Column("address", sa.String(255), nullable=True),
        sa.Column("city", sa.String(120), nullable=True),
        sa.Column("state", sa.String(120), nullable=True),
        sa.Column("provider", sa.String(50), nullable=True),
        sa.Column("external_id", sa.String(255), nullable=True),
    )
    op.create_index("ix_local_listings_tenant_id", "local_listings", ["tenant_id"])

    op.create_table(
        "local_reviews",
        *_timestamp_cols(),
        sa.Column("listing_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("rating", sa.Integer, nullable=False),
        sa.Column("author", sa.String(255), nullable=True),
        sa.Column("body", sa.Text, nullable=True),
        sa.Column("source", sa.String(50), nullable=False, server_default="manual"),
        sa.Column("responded", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("response_text", sa.Text, nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_local_reviews_tenant_id", "local_reviews", ["tenant_id"])
    op.create_index("ix_local_reviews_listing_id", "local_reviews", ["listing_id"])

    op.create_table(
        "local_reputation_events",
        *_timestamp_cols(),
        sa.Column("listing_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_local_reputation_events_tenant_id", "local_reputation_events", ["tenant_id"])
    op.create_index("ix_local_reputation_events_listing_id", "local_reputation_events", ["listing_id"])

    op.create_table(
        "outbound_lists",
        *_timestamp_cols(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
    )
    op.create_index("ix_outbound_lists_tenant_id", "outbound_lists", ["tenant_id"])

    op.create_table(
        "outbound_contacts",
        *_timestamp_cols(),
        sa.Column("list_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company", sa.String(255), nullable=True),
        sa.Column("contact_name", sa.String(255), nullable=True),
        sa.Column("role", sa.String(255), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("email_normalized", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("phone_normalized", sa.String(50), nullable=True),
        sa.Column("website", sa.String(255), nullable=True),
        sa.Column("industry", sa.String(255), nullable=True),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("service_relevance", sa.Text, nullable=True),
        sa.Column("source", sa.String(20), nullable=False, server_default="MANUAL"),
        sa.Column("enrichment_status", sa.String(20), nullable=False, server_default="NOT_ENRICHED"),
        sa.Column("qualification_status", sa.String(20), nullable=False, server_default="PENDING"),
    )
    op.create_index("ix_outbound_contacts_tenant_id", "outbound_contacts", ["tenant_id"])
    op.create_index("ix_outbound_contacts_list_id", "outbound_contacts", ["list_id"])
    op.create_index("ix_outbound_contacts_email", "outbound_contacts", ["email"])
    op.create_index("ix_outbound_contacts_email_normalized", "outbound_contacts", ["email_normalized"])
    op.create_index("ix_outbound_contacts_phone_normalized", "outbound_contacts", ["phone_normalized"])

    op.create_table(
        "outbound_sequences",
        *_timestamp_cols(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
    )
    op.create_index("ix_outbound_sequences_tenant_id", "outbound_sequences", ["tenant_id"])

    op.create_table(
        "outbound_steps",
        *_timestamp_cols(),
        sa.Column("sequence_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("day_offset", sa.Integer, nullable=False),
        sa.Column("channel", sa.String(10), nullable=False, server_default="EMAIL"),
        sa.Column("subject", sa.String(255), nullable=True),
        sa.Column("body", sa.Text, nullable=True),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index("ix_outbound_steps_tenant_id", "outbound_steps", ["tenant_id"])
    op.create_index("ix_outbound_steps_sequence_id", "outbound_steps", ["sequence_id"])

    op.create_table(
        "outbound_enrollments",
        *_timestamp_cols(),
        sa.Column("sequence_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("contact_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_step_index", sa.Integer, nullable=False, server_default="0"),
        sa.UniqueConstraint("tenant_id", "sequence_id", "contact_id", name="uq_outbound_enroll_tenant_seq_contact"),
    )
    op.create_index("ix_outbound_enrollments_tenant_id", "outbound_enrollments", ["tenant_id"])
    op.create_index("ix_outbound_enrollments_sequence_id", "outbound_enrollments", ["sequence_id"])
    op.create_index("ix_outbound_enrollments_contact_id", "outbound_enrollments", ["contact_id"])

    op.create_table(
        "outbound_activities",
        *_timestamp_cols(),
        sa.Column("enrollment_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("step_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_outbound_activities_tenant_id", "outbound_activities", ["tenant_id"])
    op.create_index("ix_outbound_activities_enrollment_id", "outbound_activities", ["enrollment_id"])
    op.create_index("ix_outbound_activities_scheduled_for", "outbound_activities", ["scheduled_for"])

    op.create_table(
        "nurture_sequences",
        *_timestamp_cols(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("trigger_type", sa.String(30), nullable=False, server_default="CUSTOM"),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
    )
    op.create_index("ix_nurture_sequences_tenant_id", "nurture_sequences", ["tenant_id"])

    op.create_table(
        "nurture_enrollments",
        *_timestamp_cols(),
        sa.Column("sequence_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("lead_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "sequence_id", "lead_id", name="uq_nurture_enroll_tenant_seq_lead"),
    )
    op.create_index("ix_nurture_enrollments_tenant_id", "nurture_enrollments", ["tenant_id"])
    op.create_index("ix_nurture_enrollments_sequence_id", "nurture_enrollments", ["sequence_id"])
    op.create_index("ix_nurture_enrollments_lead_id", "nurture_enrollments", ["lead_id"])

    op.create_table(
        "nurture_activities",
        *_timestamp_cols(),
        sa.Column("enrollment_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("channel", sa.String(10), nullable=False, server_default="EMAIL"),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_nurture_activities_tenant_id", "nurture_activities", ["tenant_id"])
    op.create_index("ix_nurture_activities_enrollment_id", "nurture_activities", ["enrollment_id"])
    op.create_index("ix_nurture_activities_scheduled_for", "nurture_activities", ["scheduled_for"])

    op.create_table(
        "reactivation_campaigns",
        *_timestamp_cols(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("target_criteria", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
    )
    op.create_index("ix_reactivation_campaigns_tenant_id", "reactivation_campaigns", ["tenant_id"])

    op.create_table(
        "reactivation_candidates",
        *_timestamp_cols(),
        sa.Column("campaign_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("customer_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("lead_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("score", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("identified_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_reactivation_candidates_tenant_id", "reactivation_candidates", ["tenant_id"])
    op.create_index("ix_reactivation_candidates_campaign_id", "reactivation_candidates", ["campaign_id"])
    op.create_index("ix_reactivation_candidates_customer_id", "reactivation_candidates", ["customer_id"])
    op.create_index("ix_reactivation_candidates_lead_id", "reactivation_candidates", ["lead_id"])


def downgrade() -> None:
    op.drop_table("reactivation_candidates")
    op.drop_table("reactivation_campaigns")
    op.drop_table("nurture_activities")
    op.drop_table("nurture_enrollments")
    op.drop_table("nurture_sequences")
    op.drop_table("outbound_activities")
    op.drop_table("outbound_enrollments")
    op.drop_table("outbound_steps")
    op.drop_table("outbound_sequences")
    op.drop_table("outbound_contacts")
    op.drop_table("outbound_lists")
    op.drop_table("local_reputation_events")
    op.drop_table("local_reviews")
    op.drop_table("local_listings")
    op.drop_table("seo_opportunities")
    op.drop_table("seo_keywords")
    op.drop_table("seo_pages")
    op.drop_table("content_performance")
    op.drop_table("content_publications")
    op.drop_table("content_variants")
    op.drop_table("content_assets")
    op.drop_table("marketing_content")
    op.drop_table("campaign_conversions")
    op.drop_table("campaign_leads")
    op.drop_table("lead_attributions")
    op.drop_table("marketing_lead_sources")
    op.drop_table("marketing_spend_allocations")
    op.drop_table("marketing_spend")
    op.drop_table("campaigns")
