"""Phase 12D: process-wide IntegrationConnectionService singleton, mirroring
app/api/tool_deps.py::get_wired_event_bus()'s pattern — one place that
constructs the service and registers every provider's real verifier.

Phase 12F: registered the first real verifier — "stripe". Stripe uses a
plain secret-key credential (no OAuth), so a tenant's `connect()` call
here is simply "store this API key, encrypted, then immediately prove it
authenticates" — exactly what `IntegrationConnectionService.connect()`
already does generically. This lets a tenant connect THEIR OWN Stripe
account (checked first by `finance.create_stripe_checkout_session`,
falling back to the platform-level `Settings.STRIPE_SECRET_KEY` — see
app/tools/builtin/stripe_tools.py) rather than only ever sharing one
platform-wide key across every tenant.

Phase 14 registered a second real verifier — "google_calendar" (same
tenant-owned-OAuth-account shape as QuickBooks). Every other provider
this model is FOR (Gmail, Google Ads, Meta Ads — tenant-owned OAuth
accounts) still has no real API client built (no credentials to build
against). Registering a fake "always succeeds" verifier for those would
be exactly the fabricated-success this project's rules forbid; leaving
them unregistered means every connect/verify attempt for those providers
honestly reports "no real verifier registered" rather than a fake
CONNECTED.
"""

from functools import lru_cache

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.integrations.google_calendar_client import GoogleCalendarAPIError, GoogleCalendarClient
from app.integrations.quickbooks_client import QuickBooksAPIError, QuickBooksClient
from app.integrations.stripe_client import StripeClient
from app.services.integration_connection_service import IntegrationConnectionService
from app.services.notification_service import NotificationService


async def _stripe_verifier(credential: dict) -> tuple[bool, str]:
    """Real verification: a tenant's stored `{"secret_key": "sk_..."}` is
    checked with a real GET /v1/balance call, same as the platform-level
    StripeAdapter.check_status() (app/integrations/adapters.py)."""
    secret_key = credential.get("secret_key")
    if not secret_key:
        return False, "credential missing required 'secret_key' field"
    client = StripeClient(secret_key)
    ok = await client.verify_connection()
    if ok:
        settings = get_settings()
        return True, f"verified via GET /v1/balance (timeout={settings.STRIPE_TIMEOUT_SECONDS}s)"
    return False, "secret_key rejected by Stripe's API"


async def _quickbooks_verifier(credential: dict) -> tuple[bool, str]:
    """Phase 13: real verification for a tenant's stored QuickBooks OAuth
    tokens — a real GET .../companyinfo/{realmId} call, mirroring the
    Stripe verifier's shape. `realm_id` isn't part of the encrypted
    credential (it's stored on the connection row's own
    external_account_id column, set by the OAuth callback) — this
    verifier is only ever invoked with it already resolved by the caller
    embedding it into the credential dict passed to `connect()`/`verify()`
    time, since `IntegrationConnectionService.verify()` only hands the
    verifier the encrypted_credential's own decrypted contents. To keep
    the verifier self-sufficient, the OAuth callback stores `realm_id`
    inside the encrypted credential blob too (alongside access_token/
    refresh_token), not only in the plaintext external_account_id column."""
    access_token = credential.get("access_token")
    realm_id = credential.get("realm_id")
    if not access_token or not realm_id:
        return False, "credential missing required 'access_token'/'realm_id' fields"
    client = QuickBooksClient()
    try:
        info = await client.get_company_info(access_token=access_token, realm_id=realm_id)
    except QuickBooksAPIError as exc:
        return False, f"QuickBooks rejected the connection: {exc}"
    return True, f"verified via GET companyinfo (company: {info.CompanyName or realm_id})"


async def _google_calendar_verifier(credential: dict) -> tuple[bool, str]:
    """Phase 14: real verification for a tenant's stored Google Calendar
    OAuth tokens — a real GET /calendars/primary call, mirroring the
    QuickBooks verifier's shape exactly (no realm-id-equivalent needed;
    the access_token alone identifies the calendar owner to Google)."""
    access_token = credential.get("access_token")
    if not access_token:
        return False, "credential missing required 'access_token' field"
    client = GoogleCalendarClient()
    try:
        calendar = await client.get_calendar(access_token=access_token, calendar_id="primary")
    except GoogleCalendarAPIError as exc:
        return False, f"Google Calendar rejected the connection: {exc}"
    return True, f"verified via GET calendars/primary (calendar: {calendar.summary or calendar.id})"


@lru_cache
def get_integration_connection_service() -> IntegrationConnectionService:
    service = IntegrationConnectionService(async_session_maker, NotificationService(async_session_maker))
    service.register_verifier("stripe", _stripe_verifier)
    service.register_verifier("quickbooks", _quickbooks_verifier)
    service.register_verifier("google_calendar", _google_calendar_verifier)
    return service
