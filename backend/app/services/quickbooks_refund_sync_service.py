"""Phase 18: pushes a real, completed Klaros Refund to a tenant's
connected QuickBooks Online company as a QuickBooks RefundReceipt applied
against the original Payment it reverses — completing the accounting
side of the refund lifecycle (Phase 12C/12F built the real Stripe refund
+ Klaros Refund bookkeeping; Phase 17 built Payment→QuickBooks sync;
this closes the loop from Refund back into the tenant's real books).

Represented as a QuickBooks RefundReceipt, not a CreditMemo or a
void/edit of the original Payment:
- A CreditMemo represents an unapplied credit toward a customer's FUTURE
  purchases — the wrong model for money that has genuinely already left
  the business via a real Stripe refund.
- Voiding/editing the original Payment would destroy the historical
  record of what was actually charged and cannot correctly represent a
  PARTIAL refund (Refund.amount can be less than Payment.amount — see
  PaymentService.request_refund's own overpayment guard).
- RefundReceipt is QuickBooks' own documented object for exactly this
  scenario: money already received, now being refunded back.

The genuine precondition this service enforces is that the ORIGINAL
Payment must already have been synced to QuickBooks itself
(`Payment.quickbooks_payment_id` set — see Phase 17's
QuickBooksPaymentSyncService) — a refund cannot reference a QuickBooks
Payment that was never created. As of Phase 17, only Stripe quote-deposit
payments have a sync path to get that field populated; a refund against
an ordinary (non-deposit) Stripe invoice payment will therefore fail
this precondition today with a specific, honest, distinguishable error —
a pre-existing Phase 17 scope boundary, not something this phase
silently works around or expands.

Every accounting value (amount, QBO customer id, QBO payment id) is read
from Klaros' own persisted, tenant-scoped records — never accepted as an
argument from a caller beyond `refund_id` itself.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.integrations.credential_store import decrypt_credential
from app.integrations.quickbooks_client import QuickBooksAPIError, QuickBooksClient, QuickBooksErrorType
from app.models.crm import Customer
from app.models.finance import Payment, Refund, RefundStatus
from app.models.integration import ConnectionStatus
from app.services.integration_connection_service import IntegrationConnectionService

_PROVIDER = "quickbooks"


class RefundNotFoundError(Exception):
    pass


class RefundNotCompletedError(Exception):
    pass


class PaymentNotFoundError(Exception):
    pass


class PaymentNotSyncedError(Exception):
    pass


class CustomerNotSyncedError(Exception):
    pass


class QuickBooksNotConnectedError(Exception):
    pass


@dataclass
class QuickBooksRefundSyncResult:
    already_synced: bool
    quickbooks_refund_receipt_id: str


class QuickBooksRefundSyncService:
    def __init__(self, session_factory: async_sessionmaker, connection_service: IntegrationConnectionService) -> None:
        self._session_factory = session_factory
        self._connection_service = connection_service

    async def sync_refund_to_quickbooks(
        self, tenant_id: uuid.UUID, refund_id: uuid.UUID
    ) -> QuickBooksRefundSyncResult:
        # Read-only resolution first — a bad request (wrong tenant, wrong
        # state, unsynced payment/customer) fails before any QuickBooks
        # API call, matching the Phase 17 payment-sync precedent exactly.
        #
        # Same real-PostgreSQL-proven concurrency fix as the invoice and
        # payment sync services: a `pg_advisory_xact_lock` keyed on this
        # exact refund, held for the life of one session/transaction
        # spanning the whole operation including the external QuickBooks
        # call, closes the same class of duplicate-RefundReceipt race.
        async with self._session_factory() as session:
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                lock_key = f"quickbooks-sync-refund:{tenant_id}:{refund_id}"
                await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": lock_key})

            refund = await session.get(Refund, refund_id)
            if refund is None or refund.tenant_id != tenant_id:
                raise RefundNotFoundError("Refund not found")

            # Idempotent: a Refund that already carries a QBO id is a
            # safe no-op — never a second QuickBooks RefundReceipt.
            if refund.quickbooks_refund_receipt_id:
                return QuickBooksRefundSyncResult(
                    already_synced=True, quickbooks_refund_receipt_id=refund.quickbooks_refund_receipt_id
                )

            if refund.status != RefundStatus.COMPLETED:
                raise RefundNotCompletedError(
                    f"Refund is {refund.status}, not COMPLETED — nothing to sync"
                )

            payment = await session.get(Payment, refund.payment_id)
            if payment is None or payment.tenant_id != tenant_id:
                raise PaymentNotFoundError("Refund's payment not found")
            if not payment.quickbooks_payment_id:
                raise PaymentNotSyncedError(
                    "The original payment has not yet been synced to QuickBooks — sync it first "
                    "(see finance.sync_deposit_payment_to_quickbooks)"
                )
            qb_payment_id = payment.quickbooks_payment_id

            customer = await session.get(Customer, payment.customer_id)
            if customer is None or customer.tenant_id != tenant_id:
                raise CustomerNotSyncedError("Payment's customer not found")
            if customer.external_provider != _PROVIDER or not customer.external_id:
                raise CustomerNotSyncedError("Customer has not yet been synced to QuickBooks")
            qb_customer_id = customer.external_id

            # Refund amount comes exclusively from the Refund row itself
            # (never Payment.amount — a refund can be partial).
            amount = refund.amount

            result = await self._sync_within_lock(
                session, tenant_id=tenant_id, refund=refund, qb_customer_id=qb_customer_id,
                qb_payment_id=qb_payment_id, amount=amount,
            )
            await session.commit()
            return result

    async def _sync_within_lock(
        self, session, *, tenant_id, refund, qb_customer_id, qb_payment_id, amount,
    ) -> QuickBooksRefundSyncResult:
        connection = await self._connection_service.get_connection(tenant_id, _PROVIDER)
        if connection is None or connection.status != ConnectionStatus.CONNECTED or not connection.encrypted_credential:
            raise QuickBooksNotConnectedError(
                "QuickBooks is not connected for this tenant — connect it via Settings → Integrations first"
            )
        realm_id = connection.external_account_id
        if not realm_id:
            raise QuickBooksNotConnectedError("QuickBooks connection is missing its company (realm) id")

        credential = decrypt_credential(connection.encrypted_credential)
        access_token = credential.get("access_token")
        refresh_token = credential.get("refresh_token")
        if not access_token or not refresh_token:
            raise QuickBooksNotConnectedError("Stored QuickBooks credential is missing access_token/refresh_token")

        client = QuickBooksClient()

        async def _refresh_and_persist() -> str:
            token_response = await client.refresh_access_token(refresh_token=refresh_token)
            await self._connection_service.connect(
                tenant_id, _PROVIDER,
                {"access_token": token_response.access_token, "refresh_token": token_response.refresh_token},
                created_by=connection.created_by, external_account_id=realm_id, scopes=connection.scopes,
            )
            return token_response.access_token

        # Deterministic per-Refund request id — same Intuit-documented
        # write-deduplication mechanism as the Phase 17 Payment sync (see
        # QuickBooksClient.create_refund_receipt's docstring).
        request_id = f"klaros-refund-{refund.id}"

        try:
            qb_refund = await client.create_refund_receipt(
                access_token=access_token, realm_id=realm_id, customer_id=qb_customer_id,
                payment_id=qb_payment_id, amount=float(amount), request_id=request_id,
            )
        except QuickBooksAPIError as exc:
            if exc.error_type == QuickBooksErrorType.AUTHENTICATION:
                access_token = await _refresh_and_persist()
                qb_refund = await client.create_refund_receipt(
                    access_token=access_token, realm_id=realm_id, customer_id=qb_customer_id,
                    payment_id=qb_payment_id, amount=float(amount), request_id=request_id,
                )
            else:
                raise

        refund.quickbooks_refund_receipt_id = qb_refund.Id

        return QuickBooksRefundSyncResult(already_synced=False, quickbooks_refund_receipt_id=qb_refund.Id)
