"""Provider adapters (section 11, real-verification depth added Phase 12C).

Every adapter's get_status() reflects real configuration state — if the
credential env vars are unset, it reports NOT_CONNECTED. Nothing here
fabricates a provider API response. Adapters that have a real API client
(Stripe, Twilio, SendGrid, Anthropic, OpenAI as of Phase 12C) additionally
implement check_status(), which makes one real, cheap, read-only API call
to distinguish "credential is set" from "credential actually works" —
see IntegrationProvider.check_status() in app/integrations/base.py.
Adapters without a real client yet (QuickBooks, ServiceTitan, Jobber,
Google Ads, Meta Ads, Gmail) still only ever report NOT_CONNECTED/ERROR,
honestly, via the base get_status() fallback.
"""

from app.core.config import get_settings
from app.integrations.base import (
    AIProvider,
    CalendarProvider,
    CommunicationProvider,
    ConnectionStatus,
    CRMProvider,
    FinanceProvider,
    IntegrationStatus,
    MarketingProvider,
    OperationsProvider,
    ProcurementProvider,
)


class QuickBooksAdapter(FinanceProvider):
    provider_name = "quickbooks"

    def get_status(self) -> IntegrationStatus:
        s = get_settings()
        client_id = s.QUICKBOOKS_CLIENT_ID
        if not client_id:
            return IntegrationStatus(
                self.provider_name, ConnectionStatus.NOT_CONNECTED, "QUICKBOOKS_CLIENT_ID not configured"
            )
        return IntegrationStatus(self.provider_name, ConnectionStatus.ERROR, "OAuth flow not implemented")


class StripeAdapter(FinanceProvider):
    provider_name = "stripe"

    def get_status(self) -> IntegrationStatus:
        s = get_settings()
        key = s.STRIPE_SECRET_KEY
        if not key:
            return IntegrationStatus(
                self.provider_name, ConnectionStatus.NOT_CONNECTED, "STRIPE_SECRET_KEY not configured"
            )
        return IntegrationStatus(self.provider_name, ConnectionStatus.ERROR, "not yet verified against the real API")

    async def check_status(self) -> IntegrationStatus:
        """Phase 12C: a real GET /v1/balance call — the cheapest read-only
        endpoint that proves the key actually authenticates, not just that
        it's non-empty."""
        s = get_settings()
        if not s.STRIPE_SECRET_KEY:
            return IntegrationStatus(
                self.provider_name, ConnectionStatus.NOT_CONNECTED, "STRIPE_SECRET_KEY not configured"
            )
        from app.integrations.stripe_client import StripeClient

        client = StripeClient(s.STRIPE_SECRET_KEY)
        if await client.verify_connection():
            return IntegrationStatus(self.provider_name, ConnectionStatus.CONNECTED, "verified via GET /v1/balance")
        return IntegrationStatus(
            self.provider_name, ConnectionStatus.ERROR, "STRIPE_SECRET_KEY set but rejected by Stripe's API"
        )


class ServiceTitanAdapter(OperationsProvider):
    provider_name = "servicetitan"

    def get_status(self) -> IntegrationStatus:
        s = get_settings()
        client_id = s.SERVICETITAN_CLIENT_ID
        if not client_id:
            return IntegrationStatus(
                self.provider_name,
                ConnectionStatus.NOT_CONNECTED,
                "SERVICETITAN_CLIENT_ID not configured",
            )
        return IntegrationStatus(self.provider_name, ConnectionStatus.ERROR, "OAuth flow not implemented")


class JobberAdapter(OperationsProvider):
    provider_name = "jobber"

    def get_status(self) -> IntegrationStatus:
        s = get_settings()
        client_id = s.JOBBER_CLIENT_ID
        if not client_id:
            return IntegrationStatus(
                self.provider_name, ConnectionStatus.NOT_CONNECTED, "JOBBER_CLIENT_ID not configured"
            )
        return IntegrationStatus(self.provider_name, ConnectionStatus.ERROR, "OAuth flow not implemented")


class GoogleAdsAdapter(MarketingProvider):
    provider_name = "google_ads"

    def get_status(self) -> IntegrationStatus:
        s = get_settings()
        token = s.GOOGLE_ADS_DEVELOPER_TOKEN
        if not token:
            return IntegrationStatus(
                self.provider_name,
                ConnectionStatus.NOT_CONNECTED,
                "GOOGLE_ADS_DEVELOPER_TOKEN not configured",
            )
        return IntegrationStatus(self.provider_name, ConnectionStatus.ERROR, "Client not implemented")


class MetaAdsAdapter(MarketingProvider):
    provider_name = "meta_ads"

    def get_status(self) -> IntegrationStatus:
        s = get_settings()
        app_id = s.META_ADS_APP_ID
        if not app_id:
            return IntegrationStatus(
                self.provider_name, ConnectionStatus.NOT_CONNECTED, "META_ADS_APP_ID not configured"
            )
        return IntegrationStatus(self.provider_name, ConnectionStatus.ERROR, "Client not implemented")


class GmailAdapter(CommunicationProvider):
    provider_name = "gmail"

    def get_status(self) -> IntegrationStatus:
        return IntegrationStatus(self.provider_name, ConnectionStatus.NOT_CONNECTED, "OAuth not configured")


class TwilioAdapter(CommunicationProvider):
    provider_name = "twilio"

    def get_status(self) -> IntegrationStatus:
        s = get_settings()
        if not s.TWILIO_ACCOUNT_SID:
            return IntegrationStatus(
                self.provider_name, ConnectionStatus.NOT_CONNECTED, "TWILIO_ACCOUNT_SID not configured"
            )
        return IntegrationStatus(self.provider_name, ConnectionStatus.ERROR, "not yet verified against the real API")

    async def check_status(self) -> IntegrationStatus:
        """Phase 12C: a real GET .../Accounts/{sid}.json call — proves the
        Account SID + Auth Token pair actually authenticates."""
        s = get_settings()
        if not s.TWILIO_ACCOUNT_SID or not s.TWILIO_AUTH_TOKEN:
            return IntegrationStatus(
                self.provider_name, ConnectionStatus.NOT_CONNECTED,
                "TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN not configured",
            )
        if not s.TWILIO_FROM_NUMBER:
            return IntegrationStatus(
                self.provider_name, ConnectionStatus.ERROR,
                "credentials set but TWILIO_FROM_NUMBER missing — cannot send",
            )
        import httpx

        url = f"https://api.twilio.com/2010-04-01/Accounts/{s.TWILIO_ACCOUNT_SID}.json"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, auth=(s.TWILIO_ACCOUNT_SID, s.TWILIO_AUTH_TOKEN))
            if response.status_code == 200:
                return IntegrationStatus(self.provider_name, ConnectionStatus.CONNECTED, "verified via GET .../Accounts/{sid}.json")
            return IntegrationStatus(
                self.provider_name, ConnectionStatus.ERROR,
                f"credentials rejected by Twilio's API (HTTP {response.status_code})",
            )
        except httpx.HTTPError as exc:
            return IntegrationStatus(self.provider_name, ConnectionStatus.ERROR, f"network error verifying Twilio: {exc}")


class SendGridAdapter(CommunicationProvider):
    provider_name = "sendgrid"

    def get_status(self) -> IntegrationStatus:
        s = get_settings()
        if not s.SENDGRID_API_KEY:
            return IntegrationStatus(
                self.provider_name, ConnectionStatus.NOT_CONNECTED, "SENDGRID_API_KEY not configured"
            )
        return IntegrationStatus(self.provider_name, ConnectionStatus.ERROR, "not yet verified against the real API")

    async def check_status(self) -> IntegrationStatus:
        """Phase 12C: a real GET /v3/user/account call — proves the API key
        actually authenticates."""
        s = get_settings()
        if not s.SENDGRID_API_KEY:
            return IntegrationStatus(
                self.provider_name, ConnectionStatus.NOT_CONNECTED, "SENDGRID_API_KEY not configured"
            )
        if not s.SENDGRID_FROM_EMAIL:
            return IntegrationStatus(
                self.provider_name, ConnectionStatus.ERROR,
                "credentials set but SENDGRID_FROM_EMAIL missing — SendGrid rejects sends from an unverified sender",
            )
        import httpx

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    "https://api.sendgrid.com/v3/user/account",
                    headers={"Authorization": f"Bearer {s.SENDGRID_API_KEY}"},
                )
            if response.status_code == 200:
                return IntegrationStatus(self.provider_name, ConnectionStatus.CONNECTED, "verified via GET /v3/user/account")
            return IntegrationStatus(
                self.provider_name, ConnectionStatus.ERROR,
                f"credentials rejected by SendGrid's API (HTTP {response.status_code})",
            )
        except httpx.HTTPError as exc:
            return IntegrationStatus(self.provider_name, ConnectionStatus.ERROR, f"network error verifying SendGrid: {exc}")


class AnthropicIntegrationAdapter(AIProvider):
    provider_name = "anthropic"

    def get_status(self) -> IntegrationStatus:
        s = get_settings()
        if not s.ANTHROPIC_API_KEY:
            return IntegrationStatus(
                self.provider_name, ConnectionStatus.NOT_CONNECTED, "ANTHROPIC_API_KEY not configured"
            )
        return IntegrationStatus(self.provider_name, ConnectionStatus.ERROR, "not yet verified against the real API")

    async def check_status(self) -> IntegrationStatus:
        """A minimal real Messages call (max_tokens=1) — Anthropic has no
        free/cheap 'list models' style endpoint, so this is the smallest
        real request that proves the key authenticates."""
        s = get_settings()
        if not s.ANTHROPIC_API_KEY:
            return IntegrationStatus(
                self.provider_name, ConnectionStatus.NOT_CONNECTED, "ANTHROPIC_API_KEY not configured"
            )
        import httpx

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": s.ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={"model": s.ANTHROPIC_MODEL, "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]},
                )
            if response.status_code == 200:
                return IntegrationStatus(
                    self.provider_name, ConnectionStatus.CONNECTED,
                    f"verified via a real Messages API call — model={s.ANTHROPIC_MODEL}",
                )
            return IntegrationStatus(
                self.provider_name, ConnectionStatus.ERROR, f"credentials rejected by Anthropic's API (HTTP {response.status_code})"
            )
        except httpx.HTTPError as exc:
            return IntegrationStatus(self.provider_name, ConnectionStatus.ERROR, f"network error verifying Anthropic: {exc}")


class OpenAIIntegrationAdapter(AIProvider):
    provider_name = "openai"

    def get_status(self) -> IntegrationStatus:
        s = get_settings()
        if not s.OPENAI_API_KEY:
            return IntegrationStatus(
                self.provider_name, ConnectionStatus.NOT_CONNECTED, "OPENAI_API_KEY not configured"
            )
        return IntegrationStatus(self.provider_name, ConnectionStatus.ERROR, "not yet verified against the real API")

    async def check_status(self) -> IntegrationStatus:
        """A real GET /v1/models call — cheap and read-only."""
        s = get_settings()
        if not s.OPENAI_API_KEY:
            return IntegrationStatus(
                self.provider_name, ConnectionStatus.NOT_CONNECTED, "OPENAI_API_KEY not configured"
            )
        import httpx

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {s.OPENAI_API_KEY}"},
                )
            if response.status_code == 200:
                return IntegrationStatus(
                    self.provider_name, ConnectionStatus.CONNECTED,
                    f"verified via GET /v1/models — model={s.OPENAI_MODEL}",
                )
            return IntegrationStatus(
                self.provider_name, ConnectionStatus.ERROR, f"credentials rejected by OpenAI's API (HTTP {response.status_code})"
            )
        except httpx.HTTPError as exc:
            return IntegrationStatus(self.provider_name, ConnectionStatus.ERROR, f"network error verifying OpenAI: {exc}")


class GenericSupplierAdapter(ProcurementProvider):
    provider_name = "supplier_procurement"

    def get_status(self) -> IntegrationStatus:
        return IntegrationStatus(
            self.provider_name, ConnectionStatus.NOT_CONNECTED, "No supplier/procurement integration configured"
        )


ALL_ADAPTERS: list[type] = [
    QuickBooksAdapter,
    StripeAdapter,
    ServiceTitanAdapter,
    JobberAdapter,
    GoogleAdsAdapter,
    MetaAdsAdapter,
    GmailAdapter,
    TwilioAdapter,
    SendGridAdapter,
    AnthropicIntegrationAdapter,
    OpenAIIntegrationAdapter,
    GenericSupplierAdapter,
]
