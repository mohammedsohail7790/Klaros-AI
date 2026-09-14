"""Phase 13: real, tenant-scoped invoice sync to a tenant's own connected
QuickBooks Online company — exposed through the same ToolRegistry ->
permission -> tenant -> policy -> execution -> audit pipeline as every
other tool, no provider-specific shortcut. `AUTO` policy: this tool moves
no money and is idempotent (an already-synced invoice is a safe no-op,
see `QuickBooksSyncService`), the same reasoning already used for
`finance.create_stripe_checkout_session` and `finance.send_invoice`.
"""

import uuid

from pydantic import BaseModel, Field

from app.integrations.quickbooks_client import QuickBooksAPIError
from app.models.rbac import Permission
from app.services.quickbooks_payment_sync_service import (
    AllocationSpansMultipleCustomersError,
    CustomerNotSyncedError,
    InvoiceNotYetCreatedError,
    InvoiceNotYetSyncedError,
    JobNotYetCreatedError,
    NoInvoiceAssociatedError,
    NotADepositPaymentError,
    NotAnInvoicePaymentError,
    NotAStripePaymentError,
    PaymentNotFoundError,
    PaymentNotSucceededError,
    QuickBooksNotConnectedError as PaymentSyncQuickBooksNotConnectedError,
    QuickBooksPaymentSyncService,
    QuoteNotFoundError as PaymentSyncQuoteNotFoundError,
)
from app.services.quickbooks_refund_sync_service import (
    CustomerNotSyncedError as RefundSyncCustomerNotSyncedError,
    PaymentNotFoundError as RefundSyncPaymentNotFoundError,
    PaymentNotSyncedError,
    QuickBooksNotConnectedError as RefundSyncQuickBooksNotConnectedError,
    QuickBooksRefundSyncService,
    RefundNotCompletedError,
    RefundNotFoundError,
)
from app.services.quickbooks_import_service import QuickBooksImportService
from app.services.quickbooks_sync_service import (
    InvoiceNotSyncableError,
    QuickBooksNotConnectedError,
    QuickBooksSyncService,
)
from app.tools.base import ExecutionContext, Tool
from app.tools.errors import ToolError


class SyncInvoiceToQuickBooksInput(BaseModel):
    invoice_id: uuid.UUID


class SyncInvoiceToQuickBooksOutput(BaseModel):
    quickbooks_invoice_id: str
    quickbooks_customer_id: str
    already_synced: bool


class SyncInvoiceToQuickBooks(Tool):
    name = "finance.sync_invoice_to_quickbooks"
    description = "Push an approved/sent/paid invoice to this tenant's connected QuickBooks Online company."
    input_schema = SyncInvoiceToQuickBooksInput
    output_schema = SyncInvoiceToQuickBooksOutput
    required_permission = Permission.SEND_INVOICE

    def __init__(self, sync_service: QuickBooksSyncService) -> None:
        self._sync_service = sync_service

    async def execute(
        self, input: SyncInvoiceToQuickBooksInput, context: ExecutionContext
    ) -> SyncInvoiceToQuickBooksOutput:
        try:
            result = await self._sync_service.sync_invoice(context.tenant_id, input.invoice_id)
        except (QuickBooksNotConnectedError, InvoiceNotSyncableError) as exc:
            raise ToolError(str(exc)) from exc
        except QuickBooksAPIError as exc:
            raise ToolError(f"QuickBooks sync failed: {exc}") from exc
        return SyncInvoiceToQuickBooksOutput(
            quickbooks_invoice_id=result.quickbooks_invoice_id,
            quickbooks_customer_id=result.quickbooks_customer_id,
            already_synced=result.already_synced,
        )


class SyncDepositPaymentToQuickBooksInput(BaseModel):
    payment_id: uuid.UUID


class SyncDepositPaymentToQuickBooksOutput(BaseModel):
    quickbooks_payment_id: str
    already_synced: bool


class SyncDepositPaymentToQuickBooks(Tool):
    """The staff-triggered counterpart to the automatic best-effort sync
    already attempted when a deposit is paid (see app/events/
    finance_handlers.py's QUOTE_DEPOSIT_PAID subscriber) — same
    underlying service, same result either way. Exists because the
    automatic attempt will fail deterministically (not a transient error)
    whenever it runs before the job's invoice has been created and synced
    to QuickBooks; once that's done, this tool (or a replay of the
    dead-lettered event) completes the sync. `AUTO` policy: idempotent by
    construction (see QuickBooksPaymentSyncService), same reasoning as
    `finance.sync_invoice_to_quickbooks`."""

    name = "finance.sync_deposit_payment_to_quickbooks"
    description = "Push a paid Stripe quote-deposit Payment to this tenant's connected QuickBooks Online company, applied against its job's already-synced invoice."
    input_schema = SyncDepositPaymentToQuickBooksInput
    output_schema = SyncDepositPaymentToQuickBooksOutput
    required_permission = Permission.SEND_INVOICE

    def __init__(self, payment_sync_service: QuickBooksPaymentSyncService) -> None:
        self._payment_sync_service = payment_sync_service

    async def execute(
        self, input: SyncDepositPaymentToQuickBooksInput, context: ExecutionContext
    ) -> SyncDepositPaymentToQuickBooksOutput:
        try:
            result = await self._payment_sync_service.sync_deposit_payment(context.tenant_id, input.payment_id)
        except (
            PaymentNotFoundError, NotADepositPaymentError, PaymentNotSucceededError,
            PaymentSyncQuoteNotFoundError, JobNotYetCreatedError, InvoiceNotYetCreatedError,
            InvoiceNotYetSyncedError, CustomerNotSyncedError, PaymentSyncQuickBooksNotConnectedError,
        ) as exc:
            raise ToolError(str(exc)) from exc
        except QuickBooksAPIError as exc:
            raise ToolError(f"QuickBooks payment sync failed: {exc}") from exc
        return SyncDepositPaymentToQuickBooksOutput(
            quickbooks_payment_id=result.quickbooks_payment_id, already_synced=result.already_synced,
        )


class SyncInvoicePaymentToQuickBooksInput(BaseModel):
    payment_id: uuid.UUID


class SyncInvoicePaymentToQuickBooksOutput(BaseModel):
    quickbooks_payment_id: str
    already_synced: bool


class SyncInvoicePaymentToQuickBooks(Tool):
    """Phase 19: the ordinary-invoice-payment counterpart to
    `SyncDepositPaymentToQuickBooks` — same service, same `AUTO` policy/
    permission reasoning, same idempotent-by-construction guarantee.
    Staff-triggered counterpart to the automatic best-effort attempt on
    `EventType.PAYMENT_RECEIVED` (see app/events/finance_handlers.py)."""

    name = "finance.sync_invoice_payment_to_quickbooks"
    description = "Push a paid Stripe invoice payment to this tenant's connected QuickBooks Online company, applied against its already-synced invoice."
    input_schema = SyncInvoicePaymentToQuickBooksInput
    output_schema = SyncInvoicePaymentToQuickBooksOutput
    required_permission = Permission.SEND_INVOICE

    def __init__(self, payment_sync_service: QuickBooksPaymentSyncService) -> None:
        self._payment_sync_service = payment_sync_service

    async def execute(
        self, input: SyncInvoicePaymentToQuickBooksInput, context: ExecutionContext
    ) -> SyncInvoicePaymentToQuickBooksOutput:
        try:
            result = await self._payment_sync_service.sync_invoice_payment_to_quickbooks(
                context.tenant_id, input.payment_id
            )
        except (
            PaymentNotFoundError, NotAnInvoicePaymentError, NotAStripePaymentError, PaymentNotSucceededError,
            NoInvoiceAssociatedError, AllocationSpansMultipleCustomersError, InvoiceNotYetCreatedError,
            InvoiceNotYetSyncedError, CustomerNotSyncedError, PaymentSyncQuickBooksNotConnectedError,
        ) as exc:
            raise ToolError(str(exc)) from exc
        except QuickBooksAPIError as exc:
            raise ToolError(f"QuickBooks payment sync failed: {exc}") from exc
        return SyncInvoicePaymentToQuickBooksOutput(
            quickbooks_payment_id=result.quickbooks_payment_id, already_synced=result.already_synced,
        )


class SyncRefundToQuickBooksInput(BaseModel):
    refund_id: uuid.UUID


class SyncRefundToQuickBooksOutput(BaseModel):
    quickbooks_refund_receipt_id: str
    already_synced: bool


class SyncRefundToQuickBooks(Tool):
    """The staff-triggered counterpart to the automatic best-effort sync
    attempted when a refund completes (see app/events/finance_handlers.py's
    PAYMENT_REFUNDED subscriber) — same underlying service, same result
    either way. `AUTO` policy: idempotent by construction, moves no NEW
    money (the real refund already happened via Stripe) — same reasoning
    as `finance.sync_deposit_payment_to_quickbooks`."""

    name = "finance.sync_refund_to_quickbooks"
    description = "Push a completed Klaros refund to this tenant's connected QuickBooks Online company as a RefundReceipt against the original payment."
    input_schema = SyncRefundToQuickBooksInput
    output_schema = SyncRefundToQuickBooksOutput
    required_permission = Permission.SEND_INVOICE

    def __init__(self, refund_sync_service: QuickBooksRefundSyncService) -> None:
        self._refund_sync_service = refund_sync_service

    async def execute(
        self, input: SyncRefundToQuickBooksInput, context: ExecutionContext
    ) -> SyncRefundToQuickBooksOutput:
        try:
            result = await self._refund_sync_service.sync_refund_to_quickbooks(context.tenant_id, input.refund_id)
        except (
            RefundNotFoundError, RefundNotCompletedError, RefundSyncPaymentNotFoundError,
            PaymentNotSyncedError, RefundSyncCustomerNotSyncedError, RefundSyncQuickBooksNotConnectedError,
        ) as exc:
            raise ToolError(str(exc)) from exc
        except QuickBooksAPIError as exc:
            raise ToolError(f"QuickBooks refund sync failed: {exc}") from exc
        return SyncRefundToQuickBooksOutput(
            quickbooks_refund_receipt_id=result.quickbooks_refund_receipt_id,
            already_synced=result.already_synced,
        )


class ImportFromQuickBooksInput(BaseModel):
    max_records: int = Field(default=300, le=1000, gt=0)


class ImportInvoiceRowOutput(BaseModel):
    quickbooks_invoice_id: str
    status: str
    invoice_id: str | None = None
    reason: str | None = None


class ImportFromQuickBooksOutput(BaseModel):
    customers_created: int
    customers_matched: int
    invoices_created: int
    invoices_skipped: int
    invoice_results: list[ImportInvoiceRowOutput]


class ImportFromQuickBooks(Tool):
    """The pull direction — brings a tenant's EXISTING QuickBooks customers
    and invoices into Klaros, the reverse of every Sync*ToQuickBooks tool
    above. AUTO policy: same reasoning as crm.bulk_import_customers/
    finance.bulk_import_invoices — a tenant's own deliberate action to
    bring in their own already-real data, not an AI proposal. Idempotent:
    a repeat call only pulls in what's new since the last run (see
    QuickBooksImportService's external_id-linked skip)."""

    name = "finance.import_from_quickbooks"
    description = "Import this tenant's existing QuickBooks customers and invoices into Klaros."
    input_schema = ImportFromQuickBooksInput
    output_schema = ImportFromQuickBooksOutput
    required_permission = Permission.MANAGE_INTEGRATIONS

    def __init__(self, import_service: QuickBooksImportService) -> None:
        self._import_service = import_service

    async def execute(self, input: ImportFromQuickBooksInput, context: ExecutionContext) -> ImportFromQuickBooksOutput:
        try:
            result = await self._import_service.import_all(context.tenant_id, max_records=input.max_records)
        except QuickBooksNotConnectedError as exc:
            raise ToolError(str(exc)) from exc
        except QuickBooksAPIError as exc:
            raise ToolError(f"QuickBooks import failed: {exc}") from exc
        return ImportFromQuickBooksOutput(
            customers_created=result.customers_created,
            customers_matched=result.customers_matched,
            invoices_created=result.invoices_created,
            invoices_skipped=result.invoices_skipped,
            invoice_results=[
                ImportInvoiceRowOutput(
                    quickbooks_invoice_id=r.quickbooks_invoice_id, status=r.status, invoice_id=r.invoice_id, reason=r.reason
                )
                for r in result.invoice_results
            ],
        )
