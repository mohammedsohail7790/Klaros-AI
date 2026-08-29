from sqlalchemy.ext.asyncio import async_sessionmaker

from app.invoice_delivery.base import InvoiceDeliveryProvider
from app.invoice_delivery.internal_test_adapter import InternalTestInvoiceDeliveryAdapter


def get_invoice_delivery_provider(session_factory: async_sessionmaker) -> InvoiceDeliveryProvider:
    # No real invoice-delivery integration is connected yet (would live here,
    # branching on settings the same way app/storage/factory.py does).
    return InternalTestInvoiceDeliveryAdapter(session_factory)
