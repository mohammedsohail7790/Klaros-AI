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

    @abstractmethod
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
        """Phase 14: same provider/document-delivery abstraction as
        `send_invoice`, extended for quotes — deliberately on the same
        interface rather than a parallel `quote_delivery/` package, since
        it is the identical capability (email a customer-facing document
        reference) for a different document type. `view_url` is the real,
        signed public link (`create_quote_view_token`) the customer uses
        to view and accept/decline — never a fabricated one."""
        ...
