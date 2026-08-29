"""section 9-10: payment provider abstraction. Real external providers
(Stripe/PayPal/Bill.com/etc.) remain NOT_CONNECTED until real, verified
credentials exist — see app/integrations/adapters.py for that pattern.
Same provider-adapter shape as app/calendar/, app/storage/, app/invoice_delivery/.
"""

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal


@dataclass
class ChargeResult:
    succeeded: bool
    provider: str
    external_id: str
    amount: Decimal


class PaymentProvider(ABC):
    provider_name: str
    is_connected: bool

    @abstractmethod
    async def charge(
        self, tenant_id: uuid.UUID, *, invoice_id: uuid.UUID, amount: Decimal, method: str
    ) -> ChargeResult: ...
