"""The pull direction of the QuickBooks integration — the counterpart to
`quickbooks_sync_service.py`/`quickbooks_payment_sync_service.py`/
`quickbooks_refund_sync_service.py`, which only ever PUSH Klaros-created
records into QuickBooks. This reads a tenant's real, existing QuickBooks
customers and invoices and creates the matching Klaros records, so a
business already running QuickBooks doesn't have to start from an empty
CRM/AR ledger — the same problem the CSV importers
(app/tools/builtin/crm_tools.py::BulkImportCustomers,
app/tools/builtin/invoice_tools.py::BulkImportInvoices) solve for a
spreadsheet export.

Idempotent by design: every pulled Customer/Invoice gets
external_provider="quickbooks" + external_id=<QBO id> stamped (the same
columns the PUSH direction already uses — see Invoice.external_id in
quickbooks_sync_service.py), so re-running an import only ever pulls in
what's new since the last run. A row whose invoice_number collides with
something already in Klaros (e.g. the same real invoice previously
brought in via CSV under the same number) is skipped via
InvoiceNumberCollisionError, never fatal to the batch — the same
protection the CSV invoice importer relies on.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.integrations.credential_store import decrypt_credential
from app.integrations.quickbooks_client import QuickBooksAPIError, QuickBooksClient, QuickBooksErrorType
from app.integrations.quickbooks_schemas import QuickBooksCustomerQueryRow, QuickBooksInvoiceQueryRow
from app.models.crm import Customer
from app.models.finance import Invoice
from app.models.integration import ConnectionStatus
from app.services.customer_matching import find_matching_customer, normalize_email, normalize_phone
from app.services.integration_connection_service import IntegrationConnectionService
from app.services.invoice_service import InvoiceNumberCollisionError, InvoiceService
from app.services.quickbooks_sync_service import QuickBooksNotConnectedError

_PROVIDER = "quickbooks"
_PAGE_SIZE = 100


@dataclass
class InvoiceImportRowResult:
    quickbooks_invoice_id: str
    status: str  # "created" | "skipped"
    invoice_id: str | None = None
    reason: str | None = None


@dataclass
class QuickBooksImportResult:
    customers_created: int = 0
    customers_matched: int = 0
    invoices_created: int = 0
    invoices_skipped: int = 0
    invoice_results: list[InvoiceImportRowResult] = field(default_factory=list)


class QuickBooksImportService:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        connection_service: IntegrationConnectionService,
        invoice_service: InvoiceService,
    ) -> None:
        self._session_factory = session_factory
        self._connection_service = connection_service
        self._invoice_service = invoice_service

    async def _get_connection_and_realm(self, tenant_id: uuid.UUID):
        connection = await self._connection_service.get_connection(tenant_id, _PROVIDER)
        if connection is None or connection.status != ConnectionStatus.CONNECTED or not connection.encrypted_credential:
            raise QuickBooksNotConnectedError(
                "QuickBooks is not connected for this tenant — connect it via Settings → Integrations first"
            )
        realm_id = connection.external_account_id
        if not realm_id:
            raise QuickBooksNotConnectedError("QuickBooks connection is missing its company (realm) id")
        return connection, realm_id

    async def _call_with_refresh(self, connection, realm_id: str, client: QuickBooksClient, call):
        """Runs `call(access_token)`, and on a QuickBooks AUTHENTICATION
        error refreshes the stored token once and retries — the same
        pattern quickbooks_sync_service.py's `_sync_within_lock` uses per
        call site, factored into one place here since a pull makes many
        sequential calls."""
        credential = decrypt_credential(connection.encrypted_credential)
        access_token = credential.get("access_token")
        refresh_token = credential.get("refresh_token")
        if not access_token or not refresh_token:
            raise QuickBooksNotConnectedError("Stored QuickBooks credential is missing access_token/refresh_token")

        try:
            return await call(access_token), access_token
        except QuickBooksAPIError as exc:
            if exc.error_type != QuickBooksErrorType.AUTHENTICATION:
                raise
            token_response = await client.refresh_access_token(refresh_token=refresh_token)
            await self._connection_service.connect(
                connection.tenant_id, _PROVIDER,
                {"access_token": token_response.access_token, "refresh_token": token_response.refresh_token},
                created_by=connection.created_by, external_account_id=realm_id, scopes=connection.scopes,
            )
            new_token = token_response.access_token
            return await call(new_token), new_token

    async def import_customers(self, tenant_id: uuid.UUID, *, max_records: int = 300) -> tuple[int, int]:
        connection, realm_id = await self._get_connection_and_realm(tenant_id)
        client = QuickBooksClient()
        created = 0
        matched = 0
        start = 1

        while start <= max_records:
            page_size = min(_PAGE_SIZE, max_records - start + 1)
            rows, _ = await self._call_with_refresh(
                connection, realm_id, client,
                lambda token, s=start, n=page_size: client.query_customers(
                    access_token=token, realm_id=realm_id, start_position=s, max_results=n
                ),
            )
            if not rows:
                break
            for row in rows:
                outcome = await self._import_one_customer(tenant_id, row)
                if outcome == "created":
                    created += 1
                elif outcome == "matched":
                    matched += 1
            if len(rows) < page_size:
                break
            start += page_size

        return created, matched

    async def _import_one_customer(self, tenant_id: uuid.UUID, row: QuickBooksCustomerQueryRow) -> str:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            already_linked = (
                await session.execute(
                    select(Customer).where(
                        Customer.tenant_id == tenant_id, Customer.external_provider == _PROVIDER, Customer.external_id == row.Id
                    )
                )
            ).scalar_one_or_none()
            if already_linked is not None:
                return "already_linked"

            email = row.PrimaryEmailAddr.Address if row.PrimaryEmailAddr else None
            phone = row.PrimaryPhone.FreeFormNumber if row.PrimaryPhone else None
            match = await find_matching_customer(session, tenant_id=tenant_id, email=email, phone=phone)
            if match is not None:
                match.external_provider = _PROVIDER
                match.external_id = row.Id
                await session.commit()
                return "matched"

            from app.services.consent_gate import ensure_not_gated

            await ensure_not_gated(self._session_factory, tenant_id, "customer_import_disabled_for_consent_gated_tenant")
            customer = Customer(
                tenant_id=tenant_id,
                name=row.DisplayName or "QuickBooks Customer",
                email=normalize_email(email),
                phone=phone,
                phone_normalized=normalize_phone(phone),
                external_provider=_PROVIDER,
                external_id=row.Id,
            )
            session.add(customer)
            await session.commit()
            return "created"

    async def import_invoices(self, tenant_id: uuid.UUID, *, max_records: int = 300) -> tuple[int, int, list[InvoiceImportRowResult]]:
        connection, realm_id = await self._get_connection_and_realm(tenant_id)
        client = QuickBooksClient()
        created = 0
        skipped = 0
        results: list[InvoiceImportRowResult] = []
        start = 1

        while start <= max_records:
            page_size = min(_PAGE_SIZE, max_records - start + 1)
            rows, _ = await self._call_with_refresh(
                connection, realm_id, client,
                lambda token, s=start, n=page_size: client.query_invoices(
                    access_token=token, realm_id=realm_id, start_position=s, max_results=n
                ),
            )
            if not rows:
                break
            for row in rows:
                result = await self._import_one_invoice(tenant_id, row)
                if result is None:
                    continue  # already linked from a previous run — not worth reporting every time
                results.append(result)
                if result.status == "created":
                    created += 1
                else:
                    skipped += 1
            if len(rows) < page_size:
                break
            start += page_size

        return created, skipped, results

    async def _resolve_customer_id(self, tenant_id: uuid.UUID, ref) -> uuid.UUID | None:
        if ref is None:
            return None
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            if ref.value:
                linked = (
                    await session.execute(
                        select(Customer).where(
                            Customer.tenant_id == tenant_id, Customer.external_provider == _PROVIDER, Customer.external_id == ref.value
                        )
                    )
                ).scalar_one_or_none()
                if linked is not None:
                    return linked.id
            if not ref.name:
                return None
            by_name = (
                await session.execute(
                    select(Customer).where(Customer.tenant_id == tenant_id, func.lower(Customer.name) == ref.name.strip().lower())
                )
            ).scalar_one_or_none()
            if by_name is not None:
                return by_name.id
            from app.services.consent_gate import ensure_not_gated

            await ensure_not_gated(self._session_factory, tenant_id, "customer_import_disabled_for_consent_gated_tenant")
            customer = Customer(tenant_id=tenant_id, name=ref.name, external_provider=_PROVIDER if ref.value else None, external_id=ref.value)
            session.add(customer)
            await session.commit()
            await session.refresh(customer)
            return customer.id

    async def _import_one_invoice(self, tenant_id: uuid.UUID, row: QuickBooksInvoiceQueryRow) -> InvoiceImportRowResult | None:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            already_linked = (
                await session.execute(
                    select(Invoice).where(
                        Invoice.tenant_id == tenant_id, Invoice.external_provider == _PROVIDER, Invoice.external_id == row.Id
                    )
                )
            ).scalar_one_or_none()
            if already_linked is not None:
                return None

        customer_id = await self._resolve_customer_id(tenant_id, row.CustomerRef)
        if customer_id is None:
            return InvoiceImportRowResult(
                quickbooks_invoice_id=row.Id, status="skipped", reason="Could not resolve a customer for this invoice"
            )

        issue_date = _parse_date(row.TxnDate) or date.today()
        due_date = _parse_date(row.DueDate) or issue_date
        amount = Decimal(str(row.TotalAmt))
        amount_paid = amount - Decimal(str(row.Balance))

        try:
            invoice = await self._invoice_service.create_imported_invoice(
                tenant_id,
                customer_id=customer_id,
                issue_date=issue_date,
                due_date=due_date,
                amount=amount,
                amount_paid=amount_paid,
                description="Imported from QuickBooks",
                invoice_number=row.DocNumber,
                external_provider=_PROVIDER,
                external_id=row.Id,
            )
        except InvoiceNumberCollisionError as exc:
            return InvoiceImportRowResult(quickbooks_invoice_id=row.Id, status="skipped", reason=str(exc))

        return InvoiceImportRowResult(quickbooks_invoice_id=row.Id, status="created", invoice_id=str(invoice.id))

    async def import_all(self, tenant_id: uuid.UUID, *, max_records: int = 300) -> QuickBooksImportResult:
        customers_created, customers_matched = await self.import_customers(tenant_id, max_records=max_records)
        invoices_created, invoices_skipped, invoice_results = await self.import_invoices(tenant_id, max_records=max_records)
        return QuickBooksImportResult(
            customers_created=customers_created,
            customers_matched=customers_matched,
            invoices_created=invoices_created,
            invoices_skipped=invoices_skipped,
            invoice_results=invoice_results,
        )


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
