"""Placeholder adapters (section 11).

Each adapter's get_status() reflects real configuration state — if the
credential env vars are unset, it reports NOT_CONNECTED. Nothing here
fabricates a provider API response. Wiring up OAuth flows, webhook
verification, and actual API calls is out of scope for Phase 2.
"""

from app.core.config import get_settings
from app.integrations.base import (
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
        return IntegrationStatus(self.provider_name, ConnectionStatus.ERROR, "Client not implemented")


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
        return IntegrationStatus(self.provider_name, ConnectionStatus.ERROR, "Client not implemented")


class SendGridAdapter(CommunicationProvider):
    provider_name = "sendgrid"

    def get_status(self) -> IntegrationStatus:
        s = get_settings()
        if not s.SENDGRID_API_KEY:
            return IntegrationStatus(
                self.provider_name, ConnectionStatus.NOT_CONNECTED, "SENDGRID_API_KEY not configured"
            )
        return IntegrationStatus(self.provider_name, ConnectionStatus.ERROR, "Client not implemented")


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
    GenericSupplierAdapter,
]
