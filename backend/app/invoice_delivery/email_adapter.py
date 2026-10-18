"""Real e-mail delivery of an invoice, used whenever the deployment has an e-mail provider.

Until now "send invoice" only wrote a log row, so a clinic that owed a commission never received the invoice. This adapter e-mails it: the number, the amount in the invoice's own currency,
the dates and how to pay or ask a question. A message to a partner clinic (a billing account the Medical Tourism module creates) goes through the clinic channel, because a clinic is a business,
not a patient, and the patient consent guard does not apply to it. Any other customer goes through the consent-guarded channel, exactly as every other message to a person does. If the customer
has no e-mail address, or the message is refused or fails, nothing pretends to be delivered: the attempt is logged by the fallback adapter and the result says it was not delivered.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.invoice_delivery.base import DeliveryResult, InvoiceDeliveryProvider
from app.invoice_delivery.internal_test_adapter import InternalTestInvoiceDeliveryAdapter
from app.models.crm import Customer
from app.models.finance import Invoice
from app.models.organization import Organization

logger = structlog.get_logger(__name__)
CLINIC_BILLING_PROVIDER = "mt_provider"


def compose(business: str, customer_name: str | None, invoice: Invoice) -> tuple[str, str]:
    """(subject, body): amount in the invoice's own currency, issue and due dates, and how to pay or ask a question."""
    subject = f"Invoice {invoice.invoice_number} from {business}"
    body = (
        f"Hello {customer_name or 'there'},\n\nPlease find invoice {invoice.invoice_number} from {business}.\n\n"
        f"Amount due: {invoice.currency} {invoice.amount_due:,.2f}\n"
        f"Issued: {invoice.issue_date.isoformat()}\nDue: {invoice.due_date.isoformat()}\n\n"
        "To pay, or to ask for our bank details, reply to this email. If anything looks wrong, reply and we will correct it.\n\n"
        f"Thank you,\n{business}"
    )
    return subject, body


class EmailInvoiceDeliveryAdapter(InvoiceDeliveryProvider):
    provider_name = "email_invoice_delivery"

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory
        self._fallback = InternalTestInvoiceDeliveryAdapter(session_factory)

    async def send_invoice(self, tenant_id: uuid.UUID, *, invoice_id: uuid.UUID, customer_email: str | None, amount: Decimal, invoice_number: str) -> DeliveryResult:
        from app.communications.base import MessageTemplate
        from app.communications.factory import get_clinic_channel, get_communication_provider

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            invoice = await session.get(Invoice, invoice_id)
            customer = await session.get(Customer, invoice.customer_id) if invoice is not None else None
            org = await session.get(Organization, tenant_id)
        to = (customer_email or (customer.email if customer else None) or "").strip()
        if invoice is None or customer is None or not to:
            await self._fallback.send_invoice(tenant_id, invoice_id=invoice_id, customer_email=customer_email, amount=amount, invoice_number=invoice_number)
            return DeliveryResult(delivered=False, provider=self.provider_name, external_reference=None)
        subject, body = compose(org.name if org is not None else "Our team", customer.name, invoice)
        try:
            if customer.external_provider == CLINIC_BILLING_PROVIDER:
                sent = bool(await get_clinic_channel(self._session_factory).send_email(tenant_id, to=to, subject=subject, body=body, template=MessageTemplate.INVOICE_SENT))
            else:
                result = await get_communication_provider(self._session_factory).deliver_email(tenant_id, to=to, subject=subject, body=body, template=MessageTemplate.INVOICE_SENT, customer_id=customer.id)
                sent = bool(result.sent)
        except Exception as exc:  # noqa: BLE001 - a delivery problem must not break the invoice itself
            logger.warning("invoice_email_failed", error=type(exc).__name__)
            sent = False
        return DeliveryResult(delivered=sent, provider=self.provider_name, external_reference=str(invoice_id) if sent else None)

    async def send_quote(self, tenant_id: uuid.UUID, *, quote_id: uuid.UUID, customer_email: str | None, amount: Decimal, quote_number: str, view_url: str) -> DeliveryResult:
        return await self._fallback.send_quote(tenant_id, quote_id=quote_id, customer_email=customer_email, amount=amount, quote_number=quote_number, view_url=view_url)
