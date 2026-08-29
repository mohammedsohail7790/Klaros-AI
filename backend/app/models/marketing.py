"""Phase 6: Marketing & Demand Generation.

Money is `Decimal` (mapped from `Numeric`) everywhere here, same convention
as `app/models/finance.py` — never `float`.

This file deliberately does NOT persist `MarketingMetric`/`MarketingSnapshot`
tables. Every marketing number (spend, leads, CAC, ROAS, revenue) is derived
on request from real rows — `MarketingSpend`, `CampaignLead`,
`CampaignConversion`, and the *existing* `Lead`/`Customer`/`Job`/`Invoice`/
`Payment` tables — by `app/services/marketing_attribution_service.py`. This
mirrors Phase 5's decision not to persist `AccountReceivable`: a derived
service is strictly more honest than a cached snapshot, since it can never
drift out of sync with the source rows, and it can say "insufficient data"
instead of returning a stale or fabricated number.

Also deliberately not duplicated here: `Lead` (already carries `source`,
`source_detail`, `campaign_id`), `Customer`, `Job`, `Invoice`, `Payment`,
`CommunicationLog`, `OperationsException`, `Event`, `AuditLog`. Marketing
reads and references those; it never re-models them.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Date, DateTime, Integer, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


# --- Campaigns -------------------------------------------------------------


class CampaignChannel(StrEnum):
    GOOGLE_ADS = "GOOGLE_ADS"
    META_ADS = "META_ADS"
    YOUTUBE_ADS = "YOUTUBE_ADS"
    LOCAL_SERVICES_ADS = "LOCAL_SERVICES_ADS"
    SEO = "SEO"
    LOCAL = "LOCAL"
    CONTENT = "CONTENT"
    OUTBOUND = "OUTBOUND"
    REFERRAL = "REFERRAL"
    OTHER = "OTHER"


class CampaignObjective(StrEnum):
    LEAD_GEN = "LEAD_GEN"
    BRAND_AWARENESS = "BRAND_AWARENESS"
    RETARGETING = "RETARGETING"
    LOCAL_VISIBILITY = "LOCAL_VISIBILITY"
    CONTENT = "CONTENT"
    REACTIVATION = "REACTIVATION"
    OTHER = "OTHER"


class CampaignStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    ARCHIVED = "ARCHIVED"


class Campaign(TenantScopedMixin, Base):
    __tablename__ = "campaigns"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    channel: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    objective: Mapped[str] = mapped_column(String(30), nullable=False, default=CampaignObjective.LEAD_GEN)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=CampaignStatus.DRAFT, index=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    monthly_budget: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    total_budget: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    # Real external campaign id once a real provider is connected — NULL
    # until then. external_provider stays "internal" for internally-run
    # campaigns (e.g. referral, local, content) that never touch an ad API.
    external_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    external_campaign_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


# --- Spend -------------------------------------------------------------


class MarketingSpend(TenantScopedMixin, Base):
    """A real spend record — one row per actual dollar amount spent,
    manually entered or (once connected) synced from a real ads platform.
    Split across campaigns via `MarketingSpendAllocation`, the same
    Payment/PaymentAllocation pattern Phase 5 uses for invoices."""

    __tablename__ = "marketing_spend"

    channel: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    spend_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="manual")
    external_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class MarketingSpendAllocation(TenantScopedMixin, Base):
    __tablename__ = "marketing_spend_allocations"

    spend_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)


# --- Attribution -------------------------------------------------------------


class AttributionModel(StrEnum):
    FIRST_TOUCH = "FIRST_TOUCH"
    LAST_TOUCH = "LAST_TOUCH"
    SOURCE_ONLY = "SOURCE_ONLY"


class MarketingLeadSource(TenantScopedMixin, Base):
    """A tenant-configured catalog of named sources (e.g. "Google Ads -
    Search", "Yelp", "Referral") so leads/campaigns tag consistently."""

    __tablename__ = "marketing_lead_sources"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    is_paid: Mapped[bool] = mapped_column(nullable=False, default=False)

    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_marketing_lead_sources_tenant_name"),)


class LeadAttribution(TenantScopedMixin, Base):
    """One row per lead — the richer attribution context `Lead.source`/
    `Lead.campaign_id` don't carry. `attribution_model` states plainly
    which model produced `campaign_id`: only ever FIRST_TOUCH, LAST_TOUCH,
    or SOURCE_ONLY — never a fabricated multi-touch weighting, since the
    underlying multi-touch event stream doesn't exist."""

    __tablename__ = "lead_attributions"

    lead_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    medium: Mapped[str | None] = mapped_column(String(100), nullable=True)
    landing_page: Mapped[str | None] = mapped_column(String(500), nullable=True)
    referral_source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    utm_source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    utm_medium: Mapped[str | None] = mapped_column(String(255), nullable=True)
    utm_campaign: Mapped[str | None] = mapped_column(String(255), nullable=True)
    utm_term: Mapped[str | None] = mapped_column(String(255), nullable=True)
    utm_content: Mapped[str | None] = mapped_column(String(255), nullable=True)
    click_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_touch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_touch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attribution_model: Mapped[str] = mapped_column(String(20), nullable=False, default=AttributionModel.SOURCE_ONLY)

    __table_args__ = (UniqueConstraint("tenant_id", "lead_id", name="uq_lead_attributions_tenant_lead"),)


class CampaignLead(TenantScopedMixin, Base):
    """A campaign's claim that it produced this lead — the entry point of
    the attribution chain. Deduplicated per (campaign, lead)."""

    __tablename__ = "campaign_leads"

    campaign_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    lead_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    attributed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "campaign_id", "lead_id", name="uq_campaign_leads_tenant_campaign_lead"),
    )


class ConversionStage(StrEnum):
    LEAD = "LEAD"
    QUALIFIED = "QUALIFIED"
    BOOKED = "BOOKED"
    JOB_CREATED = "JOB_CREATED"
    JOB_CLOSED = "JOB_CLOSED"
    INVOICED = "INVOICED"
    PAID = "PAID"


class CampaignConversion(TenantScopedMixin, Base):
    """One row per (campaign, lead) recording the furthest real stage that
    lead has reached, and the real revenue/collected amounts once an
    invoice/payment exist. Updated in place as the lead moves through the
    business loop — never a second, conflicting row for the same lead."""

    __tablename__ = "campaign_conversions"

    campaign_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    lead_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    stage: Mapped[str] = mapped_column(String(20), nullable=False, default=ConversionStage.LEAD)
    revenue_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    collected_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    updated_at_stage: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "campaign_id", "lead_id", name="uq_campaign_conversions_tenant_campaign_lead"),
    )


# --- Content -------------------------------------------------------------


class ContentStatus(StrEnum):
    IDEA = "IDEA"
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    SCHEDULED = "SCHEDULED"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    ARCHIVED = "ARCHIVED"


class ContentChannel(StrEnum):
    INSTAGRAM = "INSTAGRAM"
    TIKTOK = "TIKTOK"
    LINKEDIN = "LINKEDIN"
    YOUTUBE = "YOUTUBE"
    BLOG = "BLOG"
    FACEBOOK = "FACEBOOK"


class MarketingContent(TenantScopedMixin, Base):
    """The idea/draft record (IDEA -> ... -> PUBLISHED/ARCHIVED in one row,
    not a separate table per stage — see the module docstring's "don't
    blindly create every model" note). `source_job_id` grounds the content
    in a real completed job when one exists; content generated from a job
    must not invent outcomes/photos/testimonials beyond what that job's
    real `JobAttachment`/notes contain."""

    __tablename__ = "marketing_content"

    source_job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ContentStatus.IDEA, index=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    ai_generated: Mapped[bool] = mapped_column(nullable=False, default=False)


class ContentAsset(TenantScopedMixin, Base):
    """A media asset attached to a content item — references an *existing*
    `JobAttachment` (real photo already on disk) rather than storing a
    second copy of the file."""

    __tablename__ = "content_assets"

    content_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    job_attachment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    caption: Mapped[str | None] = mapped_column(String(500), nullable=True)


class ContentVariant(TenantScopedMixin, Base):
    """A channel-specific rendering of a `MarketingContent` idea/draft —
    the same source material, worded for Instagram vs. LinkedIn vs. Blog."""

    __tablename__ = "content_variants"

    content_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ContentStatus.DRAFT)


class ContentPublication(TenantScopedMixin, Base):
    __tablename__ = "content_publications"

    content_variant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="internal_test_publication")
    external_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ContentStatus.SCHEDULED)


class ContentPerformance(TenantScopedMixin, Base):
    """Real, manually-recorded or provider-synced numbers only. No social
    API is connected, so these rows only ever exist when a human enters
    them (or a future real integration writes them) — never fabricated."""

    __tablename__ = "content_performance"

    content_publication_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    impressions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    clicks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shares: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="manual")


# --- SEO / Local -------------------------------------------------------------


class SEOPageStatus(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"


class SEOPage(TenantScopedMixin, Base):
    __tablename__ = "seo_pages"

    service: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str] = mapped_column(String(255), nullable=False)
    url_slug: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    meta_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    meta_description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    h1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body_draft: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=SEOPageStatus.DRAFT)
    ai_generated: Mapped[bool] = mapped_column(nullable=False, default=False)


class SEOKeyword(TenantScopedMixin, Base):
    """`current_ranking`/`search_volume` are nullable and only ever set
    when a human enters a real observation — never fabricated, since no
    Google Search Console / rank-tracking provider is connected."""

    __tablename__ = "seo_keywords"

    page_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    keyword: Mapped[str] = mapped_column(String(255), nullable=False)
    target_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    search_volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_ranking: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ranking_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SEOOpportunity(TenantScopedMixin, Base):
    __tablename__ = "seo_opportunities"

    service: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str] = mapped_column(String(255), nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="MEDIUM")
    page_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class LocalListing(TenantScopedMixin, Base):
    """Internal record of a business listing. `provider`/`external_id`
    stay NULL until a real Google Business/Yelp connection exists — the
    listing row itself is real (owner-entered), the sync status is not."""

    __tablename__ = "local_listings"

    business_name: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)


class LocalReview(TenantScopedMixin, Base):
    __tablename__ = "local_reviews"

    listing_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="manual")
    responded: Mapped[bool] = mapped_column(nullable=False, default=False)
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LocalReputationEvent(TenantScopedMixin, Base):
    __tablename__ = "local_reputation_events"

    listing_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# --- Outbound & list building -------------------------------------------------------------


class ContactSource(StrEnum):
    MANUAL = "MANUAL"
    IMPORT = "IMPORT"
    CLAY = "CLAY"
    APOLLO = "APOLLO"
    PERMIT_DATA = "PERMIT_DATA"
    OTHER = "OTHER"


class EnrichmentStatus(StrEnum):
    NOT_ENRICHED = "NOT_ENRICHED"
    ENRICHED = "ENRICHED"
    FAILED = "FAILED"


class OutboundList(TenantScopedMixin, Base):
    __tablename__ = "outbound_lists"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class OutboundContact(TenantScopedMixin, Base):
    """Duplicate prevention is enforced at the service layer (same pattern
    as `Lead`/`app/services/customer_matching.py`: exact normalized email/
    phone match, never fuzzy) rather than a DB constraint, since both
    fields are optional and a contact may legitimately have neither yet."""

    __tablename__ = "outbound_contacts"

    list_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    email_normalized: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    phone_normalized: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    service_relevance: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default=ContactSource.MANUAL)
    enrichment_status: Mapped[str] = mapped_column(String(20), nullable=False, default=EnrichmentStatus.NOT_ENRICHED)
    qualification_status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")


class OutboundSequenceStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ARCHIVED = "ARCHIVED"


class OutboundSequence(TenantScopedMixin, Base):
    __tablename__ = "outbound_sequences"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=OutboundSequenceStatus.ACTIVE)


class OutboundStep(TenantScopedMixin, Base):
    __tablename__ = "outbound_steps"

    sequence_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    day_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    channel: Mapped[str] = mapped_column(String(10), nullable=False, default="EMAIL")
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class EnrollmentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"


class OutboundEnrollment(TenantScopedMixin, Base):
    __tablename__ = "outbound_enrollments"

    sequence_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    contact_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=EnrollmentStatus.ACTIVE)
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_step_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("tenant_id", "sequence_id", "contact_id", name="uq_outbound_enroll_tenant_seq_contact"),
    )


class ActivityStatus(StrEnum):
    PENDING = "PENDING"
    EXECUTED = "EXECUTED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


class OutboundActivity(TenantScopedMixin, Base):
    """Deterministic `scheduled_for` + on-demand execution — same safe
    pattern as Phase 5's `CollectionAction`. No `workflow.sleep()`."""

    __tablename__ = "outbound_activities"

    enrollment_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    step_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ActivityStatus.PENDING)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --- Nurture & reactivation -------------------------------------------------------------


class NurtureTriggerType(StrEnum):
    STALE_LEAD = "STALE_LEAD"
    UNBOOKED_QUALIFIED = "UNBOOKED_QUALIFIED"
    CUSTOM = "CUSTOM"


class NurtureSequence(TenantScopedMixin, Base):
    __tablename__ = "nurture_sequences"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(30), nullable=False, default=NurtureTriggerType.CUSTOM)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=OutboundSequenceStatus.ACTIVE)


class NurtureEnrollment(TenantScopedMixin, Base):
    __tablename__ = "nurture_enrollments"

    sequence_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    lead_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=EnrollmentStatus.ACTIVE)
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "sequence_id", "lead_id", name="uq_nurture_enroll_tenant_seq_lead"),
    )


class NurtureActivity(TenantScopedMixin, Base):
    __tablename__ = "nurture_activities"

    enrollment_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(10), nullable=False, default="EMAIL")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ActivityStatus.PENDING)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReactivationCampaign(TenantScopedMixin, Base):
    __tablename__ = "reactivation_campaigns"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=OutboundSequenceStatus.ACTIVE)


class ReactivationCandidateStatus(StrEnum):
    PENDING = "PENDING"
    CONTACTED = "CONTACTED"
    RESPONDED = "RESPONDED"
    BOOKED = "BOOKED"
    CONVERTED = "CONVERTED"
    EXCLUDED = "EXCLUDED"


class ReactivationCandidate(TenantScopedMixin, Base):
    """Deterministic eligibility only — see
    `app/services/reactivation_service.py`'s selection rules (no job in
    N days, previously-qualified-never-booked, etc.)."""

    __tablename__ = "reactivation_candidates"

    campaign_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ReactivationCandidateStatus.PENDING)
    identified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
