"""INTERNAL TEST invoice delivery adapter — logs the delivery to
`communication_logs` (channel=INVOICE_DELIVERY, real and queryable)
instead of calling a real email/PDF delivery service. External invoice
delivery integrations remain NOT_CONNECTED."""

import uuid
from decimal import Decimal

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.invoice_delivery.base import DeliveryResult, InvoiceDeliveryProvider
from app.models.communication import CommunicationLog

logger = structlog.get_logger(__name__)


class InternalTestInvoiceDeliveryAdapter(InvoiceDeliveryProvider):
    provider_name = "internal_test_invoice_delivery"

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def send_invoice(
        self,
        tenant_id: uuid.UUID,
        *,
        invoice_id: uuid.UUID,
        customer_email: str | None,
        amount: Decimal,
        invoice_number: str,
    ) -> DeliveryResult:
        recipient = customer_email or "unknown@no-email-on-file.invalid"
        async with self._session_factory() as session:
            session.add(
                CommunicationLog(
                    tenant_id=tenant_id,
                    channel="INVOICE_DELIVERY",
                    template="invoice_sent",
                    recipient=recipient,
                    subject=f"Invoice {invoice_number}",
                    body=f"Invoice {invoice_number} for ${amount} — delivered via internal test adapter.",
                    status="SENT" if customer_email else "SENT_NO_EMAIL_ON_FILE",
                    provider=self.provider_name,
                )
            )
            await session.commit()
        logger.info("internal_test_invoice_delivered", invoice_id=str(invoice_id), recipient=recipient)
        return DeliveryResult(delivered=True, provider=self.provider_name, external_reference=str(invoice_id))

    async def send_quote(
        self,
        tenant_id: uuid.UUID,
        *,
        quote_id: uuid.UUID,
        customer_email: str | None,
        amount: Decimal,
        quote_number: str,
        view_url: str,
    ) -> DeliveryResult:
        recipient = customer_email or "unknown@no-email-on-file.invalid"
        async with self._session_factory() as session:
            session.add(
                CommunicationLog(
                    tenant_id=tenant_id,
                    channel="QUOTE_DELIVERY",
                    template="quote_sent",
                    recipient=recipient,
                    subject=f"Quote {quote_number}",
                    body=(
                        f"Quote {quote_number} for ${amount} — view and respond at {view_url} "
                        "(delivered via internal test adapter)."
                    ),
                    status="SENT" if customer_email else "SENT_NO_EMAIL_ON_FILE",
                    provider=self.provider_name,
                )
            )
            await session.commit()
        logger.info("internal_test_quote_delivered", quote_id=str(quote_id), recipient=recipient)
        return DeliveryResult(delivered=True, provider=self.provider_name, external_reference=str(quote_id))
