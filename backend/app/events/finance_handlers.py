"""Finance event subscribers — mirrors app/events/operations_handlers.py's
pattern. `invoice.trigger_requested` is the entry point into the invoice
lifecycle (job close-out publishes it — see app/services/completion_service.py);
this handler turns it into an idempotent DRAFT invoice. Other finance events
notify the customer via the *existing* CommunicationProvider — no second
messaging framework.
"""

import uuid

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.communications.base import MessageTemplate
from app.communications.factory import get_communication_provider
from app.db.session import set_tenant_context
from app.events.bus import EventBus
from app.models.crm import Customer
from app.models.event import Event, EventType
from app.models.finance import Invoice, Payment
from app.services.contract_service import ContractService
from app.services.invoice_service import InvoiceService
from app.services.quickbooks_payment_sync_service import QuickBooksPaymentSyncService
from app.services.quickbooks_refund_sync_service import QuickBooksRefundSyncService

logger = structlog.get_logger(__name__)


def register_finance_handlers(bus: EventBus, session_factory: async_sessionmaker) -> None:
    invoice_service = InvoiceService(session_factory, bus)
    contract_service = ContractService(session_factory, bus)
    comms = get_communication_provider(session_factory)

    # Phase 17: local import — app.api.tool_deps_integrations owns the
    # process-wide IntegrationConnectionService singleton (real verifiers
    # registered there); imported here rather than threaded through this
    # function's signature to avoid touching every existing call site
    # (app/api/tool_deps.py and tests/conftest.py both already call
    # register_finance_handlers(bus, session_factory) with exactly two
    # positional args).
    from app.api.tool_deps_integrations import get_integration_connection_service

    payment_sync_service = QuickBooksPaymentSyncService(session_factory, get_integration_connection_service())
    refund_sync_service = QuickBooksRefundSyncService(session_factory, get_integration_connection_service())

    async def handle_invoice_trigger(event: Event) -> None:
        job_id = uuid.UUID(event.payload["job_id"]) if "job_id" in event.payload else event.entity_id
        invoice, deduped = await invoice_service.create_draft_from_job(event.tenant_id, job_id)
        logger.info("invoice_draft_from_trigger", invoice_id=str(invoice.id), job_id=str(job_id), deduped=deduped)

    async def notify_invoice_sent(event: Event) -> None:
        async with session_factory() as session:
            await set_tenant_context(session, event.tenant_id)
            invoice = await session.get(Invoice, event.entity_id)
            if invoice is None:
                return
            customer = await session.get(Customer, invoice.customer_id)
        if not customer or not customer.email:
            return
        await comms.send_email(
            event.tenant_id, to=customer.email, subject=f"Invoice {invoice.invoice_number}",
            body=f"Invoice {invoice.invoice_number} for ${invoice.total} has been sent.",
            template=MessageTemplate.INVOICE_SENT,
        )

    async def notify_payment_received(event: Event) -> None:
        payment_id = event.payload.get("payment_id")
        logger.info("payment_received_notification", payment_id=payment_id)

    async def sync_deposit_payment_to_quickbooks(event: Event) -> None:
        """Best-effort automatic attempt — reuses the EventBus's own
        bounded retry (EventBus.max_retries) and dead-letter queue for
        "visibly ERROR/PENDING_RETRY, safely retryable later" (see
        QuickBooksPaymentSyncService's module docstring and STEP 10's
        failure-semantics requirement) rather than a second background
        mechanism. This is EXPECTED to fail on first attempt for most
        quotes — no Invoice has usually been created for the job yet at
        the moment a deposit is paid (see QuoteService._convert_to_job) —
        so it will typically exhaust its retries and land in the DLQ,
        where `finance.sync_deposit_payment_to_quickbooks` (or a manual
        `EventBus.replay`) is the real, reliable completion path once
        staff has created and synced the invoice. Letting the exception
        propagate here is deliberate: this handler NEVER touches Payment/
        Quote state (already committed before this event was even
        published — the outbox pattern this whole event bus is built on),
        so a raised exception here can never roll back a real Stripe
        payment or a real Quote conversion."""
        payment_id_raw = event.payload.get("payment_id")
        if not payment_id_raw:
            return
        await payment_sync_service.sync_deposit_payment(event.tenant_id, uuid.UUID(payment_id_raw))

    async def sync_invoice_payment_to_quickbooks(event: Event) -> None:
        """Phase 19: best-effort automatic attempt for an ORDINARY
        invoice payment — reuses the ALREADY-EXISTING `EventType.
        PAYMENT_RECEIVED` (published by `PaymentService.record_payment`
        for every payment, deposit or otherwise), no new EventType
        needed. A deposit payment also publishes this event; this
        handler silently no-ops for one (`quote_id is not None`) rather
        than raising/dead-lettering, since that case is already the
        `sync_deposit_payment_to_quickbooks` handler's job above — a
        no-op here is correct behavior, not a failure to report. Same
        reasoning as the other two automatic handlers: never touches
        Payment state (already committed before this event publishes),
        so a QuickBooks failure here can never turn a real, successful
        Stripe payment into a failed one."""
        payment_id_raw = event.payload.get("payment_id")
        if not payment_id_raw:
            return
        payment_id = uuid.UUID(payment_id_raw)
        async with session_factory() as session:
            await set_tenant_context(session, event.tenant_id)
            payment = await session.get(Payment, payment_id)
        if payment is None or payment.quote_id is not None or payment.provider != "stripe":
            return
        await payment_sync_service.sync_invoice_payment_to_quickbooks(event.tenant_id, payment_id)

    async def sync_refund_to_quickbooks(event: Event) -> None:
        """Best-effort automatic attempt — same reasoning as
        sync_deposit_payment_to_quickbooks above, reusing the EventBus's
        own bounded retry + dead-letter queue rather than a second
        background mechanism. EventType.PAYMENT_REFUNDED already fires at
        exactly the right transactional boundary for BOTH refund paths
        (PaymentService.decide_refund's approved branch, and
        reconcile_external_refund for a Stripe-Dashboard-initiated
        refund) — no new EventType was needed. Never touches Refund/
        Payment state (already committed before this event was
        published), so a QuickBooks failure here can never turn a real,
        completed refund back into a failed one."""
        refund_id_raw = event.payload.get("refund_id")
        if not refund_id_raw:
            return
        await refund_sync_service.sync_refund_to_quickbooks(event.tenant_id, uuid.UUID(refund_id_raw))

    async def handle_quote_accepted_creates_contract(event: Event) -> None:
        """Genuinely connects "QUOTE ACCEPTED" to a distinct "CONTRACT"
        business object, without gating or blocking the existing
        deposit/payment pipeline (QuoteService.decide/mark_deposit_paid
        are untouched — this subscriber only ever ADDS a Contract
        alongside them). Idempotent via ContractService's own
        idempotency_key, so a redelivered event is a safe no-op."""
        quote_id_raw = event.payload.get("quote_id")
        if not quote_id_raw:
            return
        contract, deduped = await contract_service.create_from_quote(event.tenant_id, uuid.UUID(quote_id_raw))
        logger.info(
            "contract_created_from_accepted_quote",
            contract_id=str(contract.id), quote_id=quote_id_raw, deduped=deduped,
        )

    for event_type in (EventType.INVOICE_TRIGGER_REQUESTED,):
        bus.subscribe(event_type, "finance_invoice_trigger_handler", handle_invoice_trigger)

    for event_type in (EventType.QUOTE_ACCEPTED,):
        bus.subscribe(event_type, "finance_contract_from_accepted_quote", handle_quote_accepted_creates_contract)

    for event_type in (EventType.INVOICE_SENT,):
        bus.subscribe(event_type, "finance_invoice_sent_notifier", notify_invoice_sent)

    for event_type in (EventType.PAYMENT_RECEIVED,):
        bus.subscribe(event_type, "finance_payment_received_notifier", notify_payment_received)

    for event_type in (EventType.QUOTE_DEPOSIT_PAID,):
        bus.subscribe(event_type, "finance_quickbooks_deposit_payment_sync", sync_deposit_payment_to_quickbooks)

    for event_type in (EventType.PAYMENT_RECEIVED,):
        bus.subscribe(event_type, "finance_quickbooks_invoice_payment_sync", sync_invoice_payment_to_quickbooks)

    for event_type in (EventType.PAYMENT_REFUNDED,):
        bus.subscribe(event_type, "finance_quickbooks_refund_sync", sync_refund_to_quickbooks)
