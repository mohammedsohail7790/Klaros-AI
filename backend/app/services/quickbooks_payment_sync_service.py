"""Phase 17: pushes a real, already-successful Stripe deposit Payment to a
tenant's connected QuickBooks Online company as a Payment applied against
that job's already-synced QuickBooks Invoice — completing the accounting
side of the Quote -> Deposit -> Payment lifecycle (Phase 15/16 built
everything up to the Payment landing in Klaros' own ledger; this closes
the loop into the tenant's real books).

Deliberately does NOT invent a QuickBooks SalesReceipt or an unapplied
payment: at the moment a deposit is paid, there is USUALLY no Invoice yet
(the Quote has only just converted into a Job — see QuoteService.
_convert_to_job) — Invoice creation and its own QuickBooks sync
(`finance.trigger_invoice_from_job` / `finance.sync_invoice_to_quickbooks`)
are separate, staff-triggered steps, unchanged by this phase. This
service's `resolve the associated Invoice` step is therefore a genuine
precondition, not a formality: syncing a deposit payment before an
invoice exists (and has itself been pushed to QuickBooks) fails cleanly
with a specific, distinguishable, retryable error — never silently
skipped, never fabricated.

Every accounting value (amount, customer, invoice) is read from Klaros'
own persisted, tenant-scoped records — never accepted as an argument from
a caller beyond `payment_id` itself, so no caller (tool, event handler,
or otherwise) can influence what gets charged/recorded in QuickBooks.

Phase 19 extends this service (not a parallel one) to cover ORDINARY
invoice payments too — a Stripe payment allocated to an Invoice via
`PaymentAllocation` (`Payment.quote_id is None`), as opposed to a quote
deposit (`Payment.quote_id is not None`). Both paths share the same
QuickBooks-write/persist logic (`_create_and_persist_payment`); only how
the target Invoice/Customer are RESOLVED differs, since a deposit payment
resolves them via Quote -> Job -> Invoice while an ordinary invoice
payment resolves them directly via its own `PaymentAllocation` row(s).

Phase 20 extends `sync_invoice_payment_to_quickbooks` further to support a
SINGLE Klaros Payment allocated across MULTIPLE Invoices —
`PaymentService.record_payment` already supports this (it accepts a list
of allocations, each independently validated against its own invoice's
amount_due; `finance.record_test_payment` already exercises it in
practice) — so this is a real, reachable Klaros data shape, not a
theoretical one, and the QuickBooks sync must represent it correctly
rather than reject it. `QuickBooksClient.create_payment` now accepts a
list of (invoice_id, amount) line pairs, building one real QBO `Line`
entry per allocation — exactly how QuickBooks itself represents a single
payment applied across several invoices. Every allocated invoice must
independently already be synced to QuickBooks before the whole payment
can sync (a genuine precondition, same reasoning as the single-invoice
case); if any one of several isn't, the error names which invoice.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.integrations.credential_store import decrypt_credential
from app.integrations.quickbooks_client import QuickBooksAPIError, QuickBooksClient, QuickBooksErrorType
from app.models.crm import Customer
from app.models.finance import Invoice, Payment, PaymentAllocation, PaymentStatus
from app.models.integration import ConnectionStatus
from app.models.operations import Job
from app.models.quote import Quote
from app.services.integration_connection_service import IntegrationConnectionService

_PROVIDER = "quickbooks"


class PaymentNotFoundError(Exception):
    pass


class NotADepositPaymentError(Exception):
    pass


class NotAnInvoicePaymentError(Exception):
    pass


class NotAStripePaymentError(Exception):
    pass


class PaymentNotSucceededError(Exception):
    pass


class QuoteNotFoundError(Exception):
    pass


class JobNotYetCreatedError(Exception):
    pass


class InvoiceNotYetCreatedError(Exception):
    pass


class InvoiceNotYetSyncedError(Exception):
    pass


class NoInvoiceAssociatedError(Exception):
    pass


class AllocationSpansMultipleCustomersError(Exception):
    pass


class CustomerNotSyncedError(Exception):
    pass


class QuickBooksNotConnectedError(Exception):
    pass


@dataclass
class QuickBooksPaymentSyncResult:
    already_synced: bool
    quickbooks_payment_id: str


class QuickBooksPaymentSyncService:
    def __init__(self, session_factory: async_sessionmaker, connection_service: IntegrationConnectionService) -> None:
        self._session_factory = session_factory
        self._connection_service = connection_service

    async def sync_deposit_payment(
        self, tenant_id: uuid.UUID, payment_id: uuid.UUID
    ) -> QuickBooksPaymentSyncResult:
        # Step 1-4: load the Payment tenant-scoped, confirm it's a Stripe
        # deposit, confirm it's genuinely paid, resolve the Quote —
        # ALL read-only, no external call yet, so a bad request (wrong
        # tenant, wrong payment type, not actually paid) fails before any
        # QuickBooks API traffic — matches STEP 6's "cross-tenant lookup
        # should fail before any QuickBooks API call occurs."
        #
        # Same real-PostgreSQL-proven concurrency fix as
        # QuickBooksSyncService.sync_invoice and
        # GoogleCalendarSyncService.sync_appointment: the idempotency
        # check (`payment.quickbooks_payment_id`) and the final write
        # were previously in separate sessions with no lock held across
        # the real external QuickBooks call in between — a
        # `pg_advisory_xact_lock` keyed on this exact payment, held for
        # the life of one session/transaction spanning the whole
        # operation, closes the same class of duplicate-create race.
        async with self._session_factory() as session:
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                lock_key = f"quickbooks-sync-payment:{tenant_id}:{payment_id}"
                await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": lock_key})

            payment = await session.get(Payment, payment_id)
            if payment is None or payment.tenant_id != tenant_id:
                raise PaymentNotFoundError("Payment not found")

            # Idempotent: a Payment that already carries a QBO id is a
            # safe no-op — never a second QuickBooks Payment.
            if payment.quickbooks_payment_id:
                return QuickBooksPaymentSyncResult(
                    already_synced=True, quickbooks_payment_id=payment.quickbooks_payment_id
                )

            if payment.provider != "stripe" or payment.quote_id is None:
                raise NotADepositPaymentError(
                    "This Payment is not a Stripe quote-deposit payment — only deposit payments sync this way"
                )
            # Phase 18 fix: a payment that has SINCE been (partially)
            # refunded genuinely happened — it still deserves its own
            # QuickBooks Payment record (the refund itself syncs
            # separately, as a RefundReceipt referencing this Payment
            # once it exists there — see QuickBooksRefundSyncService).
            # The original, stricter `!= SUCCEEDED` check would have
            # permanently blocked ever syncing a payment that got
            # refunded before anyone got around to syncing it — a real
            # gap, not intentional; only a payment that never actually
            # succeeded (PENDING/FAILED) has nothing to sync.
            if payment.status not in (
                PaymentStatus.SUCCEEDED, PaymentStatus.PARTIALLY_REFUNDED, PaymentStatus.REFUNDED,
            ):
                raise PaymentNotSucceededError(f"Payment is {payment.status} — nothing to sync")

            quote = await session.get(Quote, payment.quote_id)
            if quote is None or quote.tenant_id != tenant_id:
                raise QuoteNotFoundError("Payment's quote not found")
            if quote.job_id is None:
                raise JobNotYetCreatedError("This quote has not yet converted to a job")

            job = await session.get(Job, quote.job_id)
            if job is None or job.tenant_id != tenant_id:
                raise JobNotYetCreatedError("Quote's job not found")

            # Step 5: resolve the associated Invoice — a genuine
            # precondition, not a formality (see module docstring). At
            # most one Invoice per job (InvoiceService.create_draft_from_job
            # is idempotency-keyed "invoice-for-job-{job_id}").
            invoice = (
                await session.execute(
                    select(Invoice).where(Invoice.tenant_id == tenant_id, Invoice.job_id == job.id)
                )
            ).scalars().first()
            if invoice is None:
                raise InvoiceNotYetCreatedError(
                    "No invoice has been created for this job yet — create one before syncing the deposit payment"
                )
            if invoice.external_provider != _PROVIDER or not invoice.external_id:
                raise InvoiceNotYetSyncedError(
                    "This job's invoice has not yet been synced to QuickBooks — sync the invoice first"
                )
            qb_invoice_id = invoice.external_id

            # Step 6: resolve the QuickBooks Customer — already populated
            # by the invoice's own sync (QuickBooksSyncService.sync_invoice
            # always creates/links the QBO Customer before the QBO
            # Invoice), so this is a defensive re-check, not a new path.
            customer = await session.get(Customer, job.customer_id)
            if customer is None or customer.tenant_id != tenant_id:
                raise CustomerNotSyncedError("Payment's customer not found")
            if customer.external_provider != _PROVIDER or not customer.external_id:
                raise CustomerNotSyncedError("Customer has not yet been synced to QuickBooks")
            qb_customer_id = customer.external_id

            result = await self._create_and_persist_payment(
                session, tenant_id, payment, qb_customer_id=qb_customer_id,
                qb_invoice_lines=[(qb_invoice_id, payment.amount)],
                request_id=f"klaros-deposit-payment-{payment.id}",
            )
            await session.commit()
            return result

    async def sync_invoice_payment_to_quickbooks(
        self, tenant_id: uuid.UUID, payment_id: uuid.UUID
    ) -> QuickBooksPaymentSyncResult:
        """The Phase 19 counterpart to `sync_deposit_payment` — an
        ORDINARY invoice payment (`Payment.quote_id is None`), resolved
        via its own `PaymentAllocation` row(s) rather than a Quote/Job.
        Phase 20: supports a payment allocated across MULTIPLE invoices —
        every allocated invoice is resolved and must independently already
        be synced to QuickBooks. Same eligibility discipline throughout:
        every check below is read-only and tenant-scoped, so a
        wrong-tenant/wrong-type/unpaid/unsynced request fails before any
        QuickBooks API call. Same advisory-lock concurrency fix as
        `sync_deposit_payment` above."""
        async with self._session_factory() as session:
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                lock_key = f"quickbooks-sync-payment:{tenant_id}:{payment_id}"
                await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": lock_key})

            payment = await session.get(Payment, payment_id)
            if payment is None or payment.tenant_id != tenant_id:
                raise PaymentNotFoundError("Payment not found")

            if payment.quickbooks_payment_id:
                return QuickBooksPaymentSyncResult(
                    already_synced=True, quickbooks_payment_id=payment.quickbooks_payment_id
                )

            if payment.quote_id is not None:
                raise NotAnInvoicePaymentError(
                    "This Payment is a quote-deposit payment — use sync_deposit_payment instead"
                )
            if payment.provider != "stripe":
                raise NotAStripePaymentError(
                    "Only Stripe payments sync to QuickBooks this way"
                )
            if payment.status not in (
                PaymentStatus.SUCCEEDED, PaymentStatus.PARTIALLY_REFUNDED, PaymentStatus.REFUNDED,
            ):
                raise PaymentNotSucceededError(f"Payment is {payment.status} — nothing to sync")

            allocations = (
                await session.execute(
                    select(PaymentAllocation)
                    .where(PaymentAllocation.tenant_id == tenant_id, PaymentAllocation.payment_id == payment.id)
                    .order_by(PaymentAllocation.created_at)
                )
            ).scalars().all()
            if not allocations:
                raise NoInvoiceAssociatedError("This payment has no invoice allocation to sync against")

            qb_invoice_lines: list[tuple[str, Decimal]] = []
            qb_customer_id: str | None = None
            for alloc in allocations:
                invoice = await session.get(Invoice, alloc.invoice_id)
                if invoice is None or invoice.tenant_id != tenant_id:
                    raise InvoiceNotYetCreatedError(f"Payment's invoice {alloc.invoice_id} not found")
                if invoice.external_provider != _PROVIDER or not invoice.external_id:
                    raise InvoiceNotYetSyncedError(
                        f"Invoice {invoice.invoice_number} has not yet been synced to QuickBooks — "
                        "sync every allocated invoice before syncing this payment"
                    )

                customer = await session.get(Customer, invoice.customer_id)
                if customer is None or customer.tenant_id != tenant_id:
                    raise CustomerNotSyncedError(f"Invoice {invoice.invoice_number}'s customer not found")
                if customer.external_provider != _PROVIDER or not customer.external_id:
                    raise CustomerNotSyncedError(
                        f"Invoice {invoice.invoice_number}'s customer has not yet been synced to QuickBooks"
                    )
                if qb_customer_id is None:
                    qb_customer_id = customer.external_id
                elif qb_customer_id != customer.external_id:
                    # A single QBO Payment has exactly one CustomerRef —
                    # if this payment's allocated invoices genuinely
                    # belong to different QuickBooks customers, there is
                    # no correct single QBO Payment to represent it as.
                    raise AllocationSpansMultipleCustomersError(
                        "This payment's allocated invoices belong to different QuickBooks customers "
                        "— cannot sync as a single QuickBooks Payment"
                    )

                qb_invoice_lines.append((invoice.external_id, alloc.amount))

            # Phase 21 fix: merge multiple PaymentAllocation rows against
            # the SAME invoice (e.g. two separate top-ups of one invoice
            # within a single payment — a real, reachable shape
            # PaymentService.record_payment doesn't prevent) into ONE
            # QuickBooks Line rather than sending duplicate `LinkedTxn`
            # entries for the same invoice, whose real QuickBooks
            # behavior (additive vs. rejected vs. last-write-wins) is
            # unverified and not worth risking — one merged Line per
            # invoice is unambiguous and always correct.
            merged_lines: dict[str, Decimal] = {}
            for qb_invoice_id, alloc_amount in qb_invoice_lines:
                merged_lines[qb_invoice_id] = merged_lines.get(qb_invoice_id, Decimal("0")) + alloc_amount
            qb_invoice_lines = list(merged_lines.items())

            result = await self._create_and_persist_payment(
                session, tenant_id, payment, qb_customer_id=qb_customer_id, qb_invoice_lines=qb_invoice_lines,
                request_id=f"klaros-invoice-payment-{payment.id}",
            )
            await session.commit()
            return result

    async def _create_and_persist_payment(
        self, session, tenant_id: uuid.UUID, payment: Payment, *, qb_customer_id: str,
        qb_invoice_lines: list[tuple[str, Decimal]], request_id: str,
    ) -> QuickBooksPaymentSyncResult:
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

        # Deterministic per-Payment request id — Intuit's own documented
        # write-deduplication mechanism (see QuickBooksClient.create_payment's
        # docstring). Closes the "QuickBooks accepted it, then this
        # process crashed before persisting quickbooks_payment_id" window
        # that a purely local (DB-only) idempotency check cannot close —
        # a retried call reaches QuickBooks with the SAME request id and
        # is deduplicated there, never creating a second real Payment.
        # NOT independently verified against a real QuickBooks account in
        # this environment (no credentials) — implemented per Intuit's
        # documented contract, never observed live.
        invoice_lines = [(invoice_id, float(amount)) for invoice_id, amount in qb_invoice_lines]
        try:
            qb_payment = await client.create_payment(
                access_token=access_token, realm_id=realm_id, customer_id=qb_customer_id,
                invoice_lines=invoice_lines, request_id=request_id,
            )
        except QuickBooksAPIError as exc:
            if exc.error_type == QuickBooksErrorType.AUTHENTICATION:
                access_token = await _refresh_and_persist()
                qb_payment = await client.create_payment(
                    access_token=access_token, realm_id=realm_id, customer_id=qb_customer_id,
                    invoice_lines=invoice_lines, request_id=request_id,
                )
            else:
                raise

        payment.quickbooks_payment_id = qb_payment.Id

        return QuickBooksPaymentSyncResult(already_synced=False, quickbooks_payment_id=qb_payment.Id)
