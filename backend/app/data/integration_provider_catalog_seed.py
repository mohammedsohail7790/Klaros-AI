"""Single source of truth for the `IntegrationProviderCatalog` seed data —
imported by both alembic/versions/0042_integration_provider_catalog.py (so
the migration and this list can never drift from each other) and
tests/test_integration_provider_catalog.py's seed-data-matches-verified-
reality regression guard (KLAROS_PHASE_1_IMPLEMENTATION_PLAN.md §1.2:
"if a stub provider's adapter is later actually implemented, this test
should be updated deliberately, not silently drift").

Status verified directly against app/integrations/adapters.py and
KLAROS_FINAL_INTEGRATION_MODEL.md at the time this list was written:

  REAL:               stripe, quickbooks, google_calendar
  STUB:                xero, google_ads, meta_ads, google_business_profile,
                       servicetitan, jobber
  WEBHOOK_NORMALIZER:  angi, thumbtack, nextdoor

Deliberately narrow — excludes twilio/sendgrid/anthropic/openai/other
AI-provider adapters, which are platform-level single-shared-credential
providers (configured once via Settings/env for the whole platform), not
providers a tenant discovers/connects via this marketplace catalog.
"""

from app.models.integration_catalog import ProviderAuthShape, ProviderImplementationStatus

SEED_PROVIDERS: list[dict] = [
    {
        "provider_key": "stripe",
        "display_name": "Stripe",
        "category": "finance",
        "implementation_status": ProviderImplementationStatus.REAL,
        "auth_shape": ProviderAuthShape.API_KEY,
        "description": "Take card payments and receive payouts.",
        "capabilities": ['payment_processing', 'billing'],
        "recommended_for_verticals": [],
        "health_check_strategy_ref": "stripe.check_status",
    },
    {
        "provider_key": "quickbooks",
        "display_name": "QuickBooks",
        "category": "finance",
        "implementation_status": ProviderImplementationStatus.REAL,
        "auth_shape": ProviderAuthShape.OAUTH2,
        "description": "Keep invoices and payments in sync with your QuickBooks books.",
        "capabilities": ['accounting', 'invoicing', 'bookkeeping'],
        "recommended_for_verticals": [],
        "health_check_strategy_ref": None,
    },
    {
        "provider_key": "google_calendar",
        "display_name": "Google Calendar",
        "category": "calendar",
        "implementation_status": ProviderImplementationStatus.REAL,
        "auth_shape": ProviderAuthShape.OAUTH2,
        "description": "Put bookings and consultations on your Google Calendar.",
        "capabilities": ['appointment_scheduling', 'scheduling', 'calendar_sync'],
        "recommended_for_verticals": [],
        "health_check_strategy_ref": None,
    },
    {
        "provider_key": "xero",
        "display_name": "Xero",
        "category": "finance",
        "implementation_status": ProviderImplementationStatus.STUB,
        "auth_shape": ProviderAuthShape.OAUTH2,
        "description": "Keep invoices and payments in sync with Xero. Not available yet.",
        "capabilities": ['accounting', 'invoicing', 'bookkeeping'],
        "recommended_for_verticals": [],
        "health_check_strategy_ref": None,
    },
    {
        "provider_key": "google_ads",
        "display_name": "Google Ads",
        "category": "marketing",
        "implementation_status": ProviderImplementationStatus.STUB,
        "auth_shape": ProviderAuthShape.OAUTH2,
        "description": "Bring Google Ads spend and campaign results into Klaros. Not available yet.",
        "capabilities": ['marketing', 'advertising', 'lead_generation'],
        "recommended_for_verticals": [],
        "health_check_strategy_ref": None,
    },
    {
        "provider_key": "meta_ads",
        "display_name": "Meta Ads",
        "category": "marketing",
        "implementation_status": ProviderImplementationStatus.STUB,
        "auth_shape": ProviderAuthShape.OAUTH2,
        "description": "Bring Facebook and Instagram ad spend and campaign results into Klaros. Not available yet.",
        "capabilities": ['marketing', 'advertising', 'lead_generation'],
        "recommended_for_verticals": [],
        "health_check_strategy_ref": None,
    },
    {
        "provider_key": "google_business_profile",
        "display_name": "Google Business Profile",
        "category": "marketing",
        "implementation_status": ProviderImplementationStatus.STUB,
        "auth_shape": ProviderAuthShape.OAUTH2,
        "description": "Manage your Google listing and reviews from Klaros. Not available yet.",
        "capabilities": ['local_listing', 'reviews', 'reputation_management'],
        "recommended_for_verticals": [],
        "health_check_strategy_ref": None,
    },
    {
        "provider_key": "servicetitan",
        "display_name": "ServiceTitan",
        "category": "operations",
        "implementation_status": ProviderImplementationStatus.STUB,
        "auth_shape": ProviderAuthShape.OAUTH2,
        "description": "Sync jobs and scheduling with ServiceTitan. Not available yet.",
        "capabilities": ['field_service_management', 'job_scheduling', 'dispatch'],
        "recommended_for_verticals": ["home_services"],
        "health_check_strategy_ref": None,
    },
    {
        "provider_key": "jobber",
        "display_name": "Jobber",
        "category": "operations",
        "implementation_status": ProviderImplementationStatus.STUB,
        "auth_shape": ProviderAuthShape.OAUTH2,
        "description": "Sync jobs and scheduling with Jobber. Not available yet.",
        "capabilities": ['field_service_management', 'job_scheduling', 'dispatch'],
        "recommended_for_verticals": ["home_services"],
        "health_check_strategy_ref": None,
    },
    {
        "provider_key": "angi",
        "display_name": "Angi",
        "category": "marketing",
        "implementation_status": ProviderImplementationStatus.WEBHOOK_NORMALIZER,
        "auth_shape": ProviderAuthShape.WEBHOOK,
        "description": "Receive leads from Angi. Leads arrive in Klaros; nothing is sent back to Angi.",
        "capabilities": ['lead_generation', 'local_listing'],
        "recommended_for_verticals": ["home_services"],
        "health_check_strategy_ref": None,
    },
    {
        "provider_key": "thumbtack",
        "display_name": "Thumbtack",
        "category": "marketing",
        "implementation_status": ProviderImplementationStatus.WEBHOOK_NORMALIZER,
        "auth_shape": ProviderAuthShape.WEBHOOK,
        "description": "Receive leads from Thumbtack. Leads arrive in Klaros; nothing is sent back to Thumbtack.",
        "capabilities": ['lead_generation', 'local_listing'],
        "recommended_for_verticals": ["home_services"],
        "health_check_strategy_ref": None,
    },
    {
        "provider_key": "nextdoor",
        "display_name": "Nextdoor",
        "category": "marketing",
        "implementation_status": ProviderImplementationStatus.WEBHOOK_NORMALIZER,
        "auth_shape": ProviderAuthShape.WEBHOOK,
        "description": "Receive leads from Nextdoor. Leads arrive in Klaros; nothing is sent back to Nextdoor.",
        "capabilities": ['lead_generation', 'local_listing'],
        "recommended_for_verticals": ["home_services"],
        "health_check_strategy_ref": None,
    },
]

# Cross-checked exactly once here, against the reconciled plan text, so a
# future edit to SEED_PROVIDERS that silently drops/renames a provider
# fails loudly (tests/test_integration_provider_catalog.py imports and
# asserts against this).
# `recommended_for_verticals` narrows who a provider is suggested to: a
# provider tagged for an industry is not offered to a business running a
# different industry module (recommendation_service.provider_fits_business).
# An untagged provider suits any business.
EXPECTED_REAL = {"stripe", "quickbooks", "google_calendar"}
EXPECTED_STUB = {"xero", "google_ads", "meta_ads", "google_business_profile", "servicetitan", "jobber"}
EXPECTED_WEBHOOK_NORMALIZER = {"angi", "thumbtack", "nextdoor"}
