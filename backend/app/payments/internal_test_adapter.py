"""INTERNAL TEST PAYMENT PROVIDER — generates deterministic test payment
references and always succeeds. This is explicitly NOT a real payment
rail: no card is charged, no money moves. Real providers (Stripe, PayPal,
Bill.com) are NOT_CONNECTED — see app/integrations/adapters.py.
"""

import uuid
from decimal import Decimal

import structlog

from app.payments.base import ChargeResult, PaymentProvider

logger = structlog.get_logger(__name__)


class InternalTestPaymentAdapter(PaymentProvider):
    """INTERNAL TEST PAYMENT PROVIDER — not a real payment rail."""

    provider_name = "internal_test_payment"
    is_connected = True  # "connected" only in the sense that this internal test rail always works

    async def charge(
        self, tenant_id: uuid.UUID, *, invoice_id: uuid.UUID, amount: Decimal, method: str
    ) -> ChargeResult:
        external_id = f"test-pay-{uuid.uuid4().hex[:16]}"
        logger.info(
            "internal_test_payment_charged",
            invoice_id=str(invoice_id),
            amount=str(amount),
            external_id=external_id,
        )
        return ChargeResult(succeeded=True, provider=self.provider_name, external_id=external_id, amount=amount)
