"""Phase 13: pushes a Klaros invoice to a tenant's own connected
QuickBooks Online company as a real QBO Invoice (creating the matching
QBO Customer first if one doesn't already exist for this Klaros
Customer). Idempotent by construction — an invoice already carrying
`external_provider="quickbooks"` is never re-synced, so a retried/duplicate
call can never create two QBO invoices for one Klaros invoice.

Token refresh is handled inline: QuickBooks access tokens expire in ~1
hour (refresh tokens ~100 days) — a 401 on the real API call triggers
exactly one refresh-and-retry, mirroring the "real, bounded retry, never
infinite" reasoning already used throughout `StripeClient`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.integrations.credential_store import decrypt_credential
from app.integrations.quickbooks_client import QuickBooksAPIError, QuickBooksClient, QuickBooksErrorType
from app.models.crm import Customer
from app.models.finance import Invoice, InvoiceLineItem, InvoiceStatus
from app.models.integration import ConnectionStatus
from app.services.integration_connection_service import IntegrationConnectionService

_PROVIDER = "quickbooks"


class QuickBooksNotConnectedError(Exception):
    pass


class InvoiceNotSyncableError(Exception):
    pass


@dataclass
class QuickBooksSyncResult:
    already_synced: bool
    quickbooks_invoice_id: str
    quickbooks_customer_id: str


class QuickBooksSyncService:
    def __init__(self, session_factory: async_sessionmaker, connection_service: IntegrationConnectionService) -> None:
        self._session_factory = session_factory
        self._connection_service = connection_service

    async def sync_invoice(self, tenant_id: uuid.UUID, invoice_id: uuid.UUID) -> QuickBooksSyncResult:
        connection = await self._connection_service.get_connection(tenant_id, _PROVIDER)
        if connection is None or connection.status != ConnectionStatus.CONNECTED or not connection.encrypted_credential:
            raise QuickBooksNotConnectedError(
                "QuickBooks is not connected for this tenant — connect it via Settings → Integrations first"
            )
        realm_id = connection.external_account_id
        if not realm_id:
            raise QuickBooksNotConnectedError("QuickBooks connection is missing its company (realm) id")

        # A real PostgreSQL concurrency test (mirroring the one that found
        # and fixed the identical bug in GoogleCalendarSyncService.
        # sync_appointment) proved this method was NOT race-safe: reading
        # "not yet synced" state in one session, making the real external
        # QuickBooks call, then writing the result back in later sessions
        # left no lock held across the gap — two genuinely concurrent
        # syncs of the SAME invoice could both create real, distinct
        # QuickBooks Invoice/Customer objects, with the second DB write
        # silently overwriting the first's external_id (never a
        # constraint violation, since external_id is a plain column on
        # the Invoice/Customer row, not a separately unique-constrained
        # table). Fixed with the same `pg_advisory_xact_lock` pattern
        # already established in app/calendar/internal_test_adapter.py
        # and this session's own Google Calendar fix — one session held
        # open across the whole operation, including the external call.
        async with self._session_factory() as session:
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                lock_key = f"quickbooks-sync-invoice:{tenant_id}:{invoice_id}"
                await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": lock_key})

            invoice = await session.get(Invoice, invoice_id)
            if invoice is None or invoice.tenant_id != tenant_id:
                raise InvoiceNotSyncableError("Invoice not found")

            if invoice.external_provider == _PROVIDER and invoice.external_id:
                return QuickBooksSyncResult(
                    already_synced=True, quickbooks_invoice_id=invoice.external_id,
                    quickbooks_customer_id="",
                )

            if invoice.status not in (InvoiceStatus.APPROVED, InvoiceStatus.SENT, InvoiceStatus.PAID, InvoiceStatus.PARTIALLY_PAID):
                raise InvoiceNotSyncableError(
                    f"Invoice {invoice.invoice_number} is {invoice.status} — only approved/sent/paid invoices sync to QuickBooks"
                )

            customer = await session.get(Customer, invoice.customer_id)
            if customer is None or customer.tenant_id != tenant_id:
                raise InvoiceNotSyncableError("Invoice's customer not found")

            line_rows = (
                await session.execute(select(InvoiceLineItem).where(InvoiceLineItem.invoice_id == invoice.id))
            ).scalars().all()
            lines: list[tuple[str, float]] = [
                (row.description, float(row.line_total if row.line_total else row.quantity * row.unit_price))
                for row in line_rows
            ] or [(f"Invoice {invoice.invoice_number}", float(invoice.total))]

            invoice_number = invoice.invoice_number
            customer_display_name = customer.company_name or customer.name
            customer_email = customer.email
            customer_phone = customer.phone
            existing_customer_external_id = (
                customer.external_id if customer.external_provider == _PROVIDER else None
            )

            result = await self._sync_within_lock(
                session, connection=connection, realm_id=realm_id, tenant_id=tenant_id,
                invoice=invoice, customer=customer, invoice_number=invoice_number,
                customer_display_name=customer_display_name, customer_email=customer_email,
                customer_phone=customer_phone, existing_customer_external_id=existing_customer_external_id,
                lines=lines,
            )
            await session.commit()
            return result

    async def _sync_within_lock(
        self, session, *, connection, realm_id, tenant_id, invoice, customer, invoice_number,
        customer_display_name, customer_email, customer_phone, existing_customer_external_id, lines,
    ) -> QuickBooksSyncResult:
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

        qb_customer_id = existing_customer_external_id
        if qb_customer_id is None:
            try:
                qb_customer = await client.create_customer(
                    access_token=access_token, realm_id=realm_id,
                    display_name=customer_display_name, email=customer_email, phone=customer_phone,
                )
            except QuickBooksAPIError as exc:
                if exc.error_type == QuickBooksErrorType.AUTHENTICATION:
                    access_token = await _refresh_and_persist()
                    qb_customer = await client.create_customer(
                        access_token=access_token, realm_id=realm_id,
                        display_name=customer_display_name, email=customer_email, phone=customer_phone,
                    )
                else:
                    raise
            qb_customer_id = qb_customer.Id
            customer.external_provider = _PROVIDER
            customer.external_id = qb_customer_id

        try:
            qb_invoice = await client.create_invoice(
                access_token=access_token, realm_id=realm_id, customer_id=qb_customer_id,
                doc_number=invoice_number, lines=lines,
            )
        except QuickBooksAPIError as exc:
            if exc.error_type == QuickBooksErrorType.AUTHENTICATION:
                access_token = await _refresh_and_persist()
                qb_invoice = await client.create_invoice(
                    access_token=access_token, realm_id=realm_id, customer_id=qb_customer_id,
                    doc_number=invoice_number, lines=lines,
                )
            else:
                raise

        invoice.external_provider = _PROVIDER
        invoice.external_id = qb_invoice.Id

        return QuickBooksSyncResult(
            already_synced=False, quickbooks_invoice_id=qb_invoice.Id, quickbooks_customer_id=qb_customer_id,
        )
