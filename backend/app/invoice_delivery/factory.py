from sqlalchemy.ext.asyncio import async_sessionmaker

from app.communications.email_config import email_configured
from app.invoice_delivery.base import InvoiceDeliveryProvider
from app.invoice_delivery.email_adapter import EmailInvoiceDeliveryAdapter
from app.invoice_delivery.internal_test_adapter import InternalTestInvoiceDeliveryAdapter


def get_invoice_delivery_provider(session_factory: async_sessionmaker) -> InvoiceDeliveryProvider:
    """Invoices are e-mailed for real when the deployment has an e-mail provider; otherwise the attempt is only logged, as before (tests, local development)."""
    if email_configured():
        return EmailInvoiceDeliveryAdapter(session_factory)
    return InternalTestInvoiceDeliveryAdapter(session_factory)
