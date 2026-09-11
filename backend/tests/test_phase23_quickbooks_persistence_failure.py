"""Phase 23: failure-injection coverage for "QuickBooks payment creation
succeeds, but the local DB write of `Payment.quickbooks_payment_id` then
fails" — explicitly called out by the Phase 23 mission's STEP 11
failure-recovery matrix (items 5/7) and not previously covered by a direct
test, only documented in prose.

This does NOT fix anything: `_create_and_persist_payment`'s architecture
already relies on a deterministic per-Payment `request_id`
(`klaros-deposit-payment-{payment_id}`) as the intended recovery
mechanism — a retried `sync_deposit_payment` call sees
`Payment.quickbooks_payment_id` still unset (since the failed commit never
persisted it) and calls QuickBooks AGAIN with the IDENTICAL request_id,
relying on Intuit's own documented write-deduplication to avoid creating a
second real QuickBooks Payment. This test proves the LOCAL half of that
guarantee (the retry sends the same key, and the local exception
propagates rather than being silently swallowed) — the PROVIDER half
(whether Intuit's API actually deduplicates on that key) remains
unverified without live QuickBooks sandbox credentials, exactly as already
documented elsewhere in this project."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_maker
from app.events.bus import EventBus
from app.integrations.quickbooks_client import QuickBooksClient
from app.integrations.quickbooks_schemas import QuickBooksCustomerResponse, QuickBooksInvoiceResponse, QuickBooksPaymentResponse
from app.models.finance import Invoice, InvoiceStatus, Payment
from app.services.integration_connection_service import IntegrationConnectionService
from app.services.invoice_service import InvoiceService
from app.services.payment_service import PaymentService
from app.services.quickbooks_payment_sync_service import QuickBooksPaymentSyncService
from app.services.quickbooks_sync_service import QuickBooksSyncService
from app.services.quote_service import QuoteService
from app.tools.base import ExecutionContext

from tests.test_quickbooks_deposit_payment_sync import _build_paid_deposit_quote, _connect_quickbooks, _sync_invoice_for_job, _ctx

pytestmark = pytest.mark.asyncio


@pytest.fixture
def connection_service() -> IntegrationConnectionService:
    from app.api.tool_deps_integrations import get_integration_connection_service

    return get_integration_connection_service()


async def test_local_commit_failure_after_quickbooks_success_stays_retryable_with_same_request_id(
    connection_service, tool_registry, event_bus, monkeypatch
) -> None:
    tenant_id = uuid.uuid4()
    _quote, job, payment = await _build_paid_deposit_quote(tool_registry, event_bus, tenant_id)
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)
    await _sync_invoice_for_job(connection_service, tenant_id, job.id, monkeypatch)

    captured_request_ids = []

    async def _fake_create_payment(self, *, access_token, realm_id, customer_id, invoice_lines, request_id=None):
        captured_request_ids.append(request_id)
        invoice_id, amount = invoice_lines[0]
        return QuickBooksPaymentResponse(Id="qb-pay-would-be-duplicate-if-not-deduped", TotalAmt=amount)

    monkeypatch.setattr(QuickBooksClient, "create_payment", _fake_create_payment)

    original_commit = AsyncSession.commit
    state = {"should_fail": True}

    async def _commit_that_fails_once_for_the_payment_write(self):
        if state["should_fail"]:
            for obj in list(self.dirty) + list(self.new):
                if isinstance(obj, Payment) and getattr(obj, "quickbooks_payment_id", None):
                    state["should_fail"] = False
                    raise RuntimeError("simulated local DB failure right after QuickBooks succeeded")
        await original_commit(self)

    monkeypatch.setattr(AsyncSession, "commit", _commit_that_fails_once_for_the_payment_write)

    service = QuickBooksPaymentSyncService(async_session_maker, connection_service)

    with pytest.raises(RuntimeError, match="simulated local DB failure"):
        await service.sync_deposit_payment(tenant_id, payment.id)

    async with async_session_maker() as session:
        refreshed = await session.get(Payment, payment.id)
        assert refreshed.quickbooks_payment_id is None, (
            "the local write must genuinely have failed — the id must not be persisted"
        )

    # Retry: the local persistence issue has now cleared (state["should_fail"]
    # was already flipped False by the raise above, simulating "the transient
    # DB issue resolved itself").
    result = await service.sync_deposit_payment(tenant_id, payment.id)
    assert result.already_synced is False
    assert result.quickbooks_payment_id == "qb-pay-would-be-duplicate-if-not-deduped"

    async with async_session_maker() as session:
        refreshed = await session.get(Payment, payment.id)
        assert refreshed.quickbooks_payment_id == "qb-pay-would-be-duplicate-if-not-deduped"

    # The LOCAL guarantee this test actually proves: both the failed attempt
    # and the retry sent the IDENTICAL deterministic request_id to
    # QuickBooks. Whether Intuit's own API genuinely deduplicates two calls
    # carrying the same request_id is a PROVIDER-side guarantee that remains
    # unverified without live QuickBooks sandbox credentials — not claimed
    # here or anywhere else in this codebase without live observation.
    assert len(captured_request_ids) == 2
    assert captured_request_ids[0] == captured_request_ids[1] == f"klaros-deposit-payment-{payment.id}"
