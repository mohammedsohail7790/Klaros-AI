"""section 8: invoice delivery provider abstraction.

Distinct from `CommunicationProvider` — this is specifically "deliver this
invoice document to the customer" (would wrap a PDF/email-with-attachment
in a real integration), while `CommunicationProvider` covers general
transactional notifications (see `app/events/finance_handlers.py`, which
uses both: this to deliver the invoice, that to notify about it).
Same provider-adapter pattern as `app/calendar/` and `app/storage/`.
"""

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal


@dataclass
class DeliveryResult:
    delivered: bool
    provider: str
    external_reference: str | None


class InvoiceDeliveryProvider(ABC):
    provider_name: str

    @abstractmethod
    async def send_invoice(
        self,
        tenant_id: uuid.UUID,
        *,
        invoice_id: uuid.UUID,
        customer_email: str | None,
        amount: Decimal,
        invoice_number: str,
    ) -> DeliveryResult: ...
