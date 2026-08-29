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

Every other provider this model is FOR (QuickBooks, Google Calendar,
Gmail, Google Ads, Meta Ads — tenant-owned OAuth accounts) still has no
real API client built (no credentials to build against). Registering a
fake "always succeeds" verifier for those would be exactly the
fabricated-success this project's rules forbid; leaving them unregistered
means every connect/verify attempt for those providers honestly reports
"no real verifier registered" rather than a fake CONNECTED.
"""

from functools import lru_cache

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.integrations.stripe_client import StripeClient
from app.services.integration_connection_service import IntegrationConnectionService


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


@lru_cache
def get_integration_connection_service() -> IntegrationConnectionService:
    service = IntegrationConnectionService(async_session_maker)
    service.register_verifier("stripe", _stripe_verifier)
    # Future: service.register_verifier("quickbooks", real_quickbooks_verifier)
    # once a real QuickBooks OAuth client exists and credentials are
    # available to verify it against.
    return service
