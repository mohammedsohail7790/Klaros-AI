"""sections 3-7: invoice lifecycle.

Totals are always recomputed backend-side from line items — the API/tool
layer never accepts a client-supplied `subtotal`/`tax`/`discount`/`total`
for anything other than display. `create_draft_from_job` is the
`invoice.trigger_requested` handler's entry point (idempotent — same job,
same invoice, never a duplicate) and only uses real job data; if there's
nothing to price from, it still creates the draft but says so in `notes`
rather than inventing an amount.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.events.bus import EventBus
from app.models.crm import Customer
from app.models.event import EventType
from app.models.finance import Invoice, InvoiceLineItem, InvoiceStatus
from app.models.operations import Job, ScopeChange, ScopeChangeStatus
from app.services.approval_helper import create_approval_request
from app.services.invoice_policy import evaluate_invoice_approval

TWO_PLACES = Decimal("0.01")
DEFAULT_DUE_DAYS = 30


def _round(value: Decimal) -> Decimal:
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


class InvoiceNotFoundError(Exception):
    pass


class JobNotFoundError(Exception):
    pass


class InvalidInvoiceTransitionError(Exception):
    pass


class InvoiceNumberCollisionError(Exception):
    """The caller supplied an invoice_number that already exists for this
    tenant (the DB's own uq_invoices_tenant_number constraint)."""

    pass


@dataclass
class LineItemInput:
    description: str
    quantity: Decimal
    unit_price: Decimal
    discount: Decimal = Decimal("0")
    tax_rate: Decimal = Decimal("0")
    job_material_id: uuid.UUID | None = None
    job_task_id: uuid.UUID | None = None


def compute_line_total(item: LineItemInput) -> Decimal:
    gross = item.quantity * item.unit_price
    after_discount = gross - item.discount
    taxed = after_discount + (after_discount * item.tax_rate)
    return _round(taxed)


def compute_totals(items: list[LineItemInput]) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Returns (subtotal, tax, discount, total) — all deterministic, all Decimal."""
    subtotal = Decimal("0")
    tax = Decimal("0")
    discount = Decimal("0")
    for item in items:
        gross = item.quantity * item.unit_price
        subtotal += gross
        discount += item.discount
        tax += (gross - item.discount) * item.tax_rate
    subtotal = _round(subtotal)
    tax = _round(tax)
    discount = _round(discount)
    total = _round(subtotal - discount + tax)
    return subtotal, tax, discount, total


class InvoiceService:
    def __init__(self, session_factory: async_sessionmaker, bus: EventBus) -> None:
        self._session_factory = session_factory
        self._bus = bus

    async def _next_invoice_number(self, session, tenant_id: uuid.UUID) -> str:
        count = (
            await session.execute(select(func.count(Invoice.id)).where(Invoice.tenant_id == tenant_id))
        ).scalar_one()
        return f"INV-{1000 + count + 1}"

    async def _replace_line_items(
        self, session, tenant_id: uuid.UUID, invoice_id: uuid.UUID, items: list[LineItemInput]
    ) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        existing = (
            await session.execute(
                select(InvoiceLineItem).where(
                    InvoiceLineItem.tenant_id == tenant_id, InvoiceLineItem.invoice_id == invoice_id
                )
            )
        ).scalars().all()
        for row in existing:
            await session.delete(row)
        await session.flush()

        for i, item in enumerate(items):
            session.add(
                InvoiceLineItem(
                    tenant_id=tenant_id,
                    invoice_id=invoice_id,
                    description=item.description,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    discount=item.discount,
                    tax_rate=item.tax_rate,
                    line_total=compute_line_total(item),
                    job_material_id=item.job_material_id,
                    job_task_id=item.job_task_id,
                    sort_order=i,
                )
            )
        return compute_totals(items)

    async def create_draft_from_job(self, tenant_id: uuid.UUID, job_id: uuid.UUID) -> tuple[Invoice, bool]:
        idempotency_key = f"invoice-for-job-{job_id}"

        async with self._session_factory() as session:
            existing = (
                await session.execute(
                    select(Invoice).where(
                        Invoice.tenant_id == tenant_id, Invoice.idempotency_key == idempotency_key
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing, True

            job = await session.get(Job, job_id)
            if job is None or job.tenant_id != tenant_id:
                raise JobNotFoundError("Job not found")

            approved_scope_changes = (
                await session.execute(
                    select(ScopeChange).where(
                        ScopeChange.tenant_id == tenant_id,
                        ScopeChange.job_id == job_id,
                        ScopeChange.status == ScopeChangeStatus.APPROVED,
                    )
                )
            ).scalars().all()

            items: list[LineItemInput] = []
            needs_review = False

            if job.estimated_revenue and job.estimated_revenue > 0:
                items.append(
                    LineItemInput(
                        description=f"{job.title} ({job.job_number})",
                        quantity=Decimal("1"),
                        unit_price=Decimal(str(job.estimated_revenue)),
                    )
                )
            else:
                needs_review = True

            for sc in approved_scope_changes:
                if sc.estimated_revenue:
                    items.append(
                        LineItemInput(
                            description=f"Scope change: {sc.description}",
                            quantity=Decimal("1"),
                            unit_price=Decimal(str(sc.estimated_revenue)),
                        )
                    )

            today = date.today()
            invoice = Invoice(
                tenant_id=tenant_id,
                invoice_number=await self._next_invoice_number(session, tenant_id),
                customer_id=job.customer_id,
                job_id=job.id,
                status=InvoiceStatus.DRAFT,
                issue_date=today,
                due_date=today + timedelta(days=DEFAULT_DUE_DAYS),
                idempotency_key=idempotency_key,
                notes=(
                    "Invoice generation requires review — no pricing data available on the job."
                    if needs_review
                    else None
                ),
            )
            session.add(invoice)
            await session.flush()

            subtotal, tax, discount, total = await self._replace_line_items(
                session, tenant_id, invoice.id, items
            )
            invoice.subtotal = subtotal
            invoice.tax = tax
            invoice.discount = discount
            invoice.total = total
            invoice.amount_due = total

            await session.commit()
            await session.refresh(invoice)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.INVOICE_CREATED,
            source="finance",
            entity_type="invoice",
            entity_id=invoice.id,
            payload={"invoice_id": str(invoice.id), "job_id": str(job_id), "needs_review": needs_review},
            idempotency_key=f"invoice-created-{invoice.id}",
        )
        return invoice, False

    async def create_manual_draft(
        self,
        tenant_id: uuid.UUID,
        *,
        customer_id: uuid.UUID,
        job_id: uuid.UUID | None,
        items: list[LineItemInput],
        due_date: date | None = None,
    ) -> Invoice:
        today = date.today()
        async with self._session_factory() as session:
            invoice = Invoice(
                tenant_id=tenant_id,
                invoice_number=await self._next_invoice_number(session, tenant_id),
                customer_id=customer_id,
                job_id=job_id,
                status=InvoiceStatus.DRAFT,
                issue_date=today,
                due_date=due_date if due_date is not None else today + timedelta(days=DEFAULT_DUE_DAYS),
            )
            session.add(invoice)
            await session.flush()

            subtotal, tax, discount, total = await self._replace_line_items(
                session, tenant_id, invoice.id, items
            )
            invoice.subtotal = subtotal
            invoice.tax = tax
            invoice.discount = discount
            invoice.total = total
            invoice.amount_due = total

            await session.commit()
            await session.refresh(invoice)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.INVOICE_CREATED,
            source="finance",
            entity_type="invoice",
            entity_id=invoice.id,
            payload={"invoice_id": str(invoice.id)},
        )
        return invoice

    async def create_imported_invoice(
        self,
        tenant_id: uuid.UUID,
        *,
        customer_id: uuid.UUID,
        issue_date: date,
        due_date: date,
        amount: Decimal,
        amount_paid: Decimal = Decimal("0"),
        description: str | None,
        invoice_number: str | None = None,
        external_provider: str | None = None,
        external_id: str | None = None,
    ) -> Invoice:
        """A single already-issued invoice from a business migrating its
        existing AR into Klaros (e.g. a QuickBooks/spreadsheet export of
        currently-owed balances) — represented as one line item for the
        given amount rather than requiring the CSV to carry a full
        itemized breakdown. Lands directly in SENT/PARTIALLY_PAID/PAID
        (whichever `amount_paid` implies) — never DRAFT/PENDING_APPROVAL,
        since this already happened outside Klaros and re-running it
        through the normal draft->approve->send lifecycle would be
        fiction, not history. Still publishes INVOICE_CREATED so AR
        aging, the Morning Brief, and collections treat it as real going
        forward — the whole point of bringing it in.
        """
        amount_paid = min(amount_paid, amount) if amount_paid > 0 else Decimal("0")
        amount_due = amount - amount_paid
        if amount_due <= 0:
            status = InvoiceStatus.PAID
        elif amount_paid > 0:
            status = InvoiceStatus.PARTIALLY_PAID
        else:
            status = InvoiceStatus.SENT

        sent_at = datetime.combine(issue_date, datetime.min.time(), tzinfo=timezone.utc)

        async with self._session_factory() as session:
            number = invoice_number or await self._next_invoice_number(session, tenant_id)
            invoice = Invoice(
                tenant_id=tenant_id,
                invoice_number=number,
                customer_id=customer_id,
                status=status,
                issue_date=issue_date,
                due_date=due_date,
                sent_at=sent_at,
                paid_at=sent_at if status == InvoiceStatus.PAID else None,
                external_provider=external_provider,
                external_id=external_id,
            )
            session.add(invoice)
            try:
                await session.flush()
            except IntegrityError as exc:
                await session.rollback()
                raise InvoiceNumberCollisionError(f"Invoice number '{number}' already exists") from exc

            item = LineItemInput(description=description or "Imported balance", quantity=Decimal("1"), unit_price=amount)
            subtotal, tax, discount, total = await self._replace_line_items(session, tenant_id, invoice.id, [item])
            invoice.subtotal = subtotal
            invoice.tax = tax
            invoice.discount = discount
            invoice.total = total
            invoice.amount_paid = amount_paid
            invoice.amount_due = total - amount_paid

            await session.commit()
            await session.refresh(invoice)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.INVOICE_CREATED,
            source="finance",
            entity_type="invoice",
            entity_id=invoice.id,
            payload={"invoice_id": str(invoice.id), "imported": True},
        )
        return invoice

    async def update_draft(
        self, tenant_id: uuid.UUID, invoice_id: uuid.UUID, items: list[LineItemInput]
    ) -> Invoice:
        async with self._session_factory() as session:
            invoice = await session.get(Invoice, invoice_id)
            if invoice is None or invoice.tenant_id != tenant_id:
                raise InvoiceNotFoundError("Invoice not found")
            if invoice.status != InvoiceStatus.DRAFT:
                raise InvalidInvoiceTransitionError("Only DRAFT invoices can be edited")

            subtotal, tax, discount, total = await self._replace_line_items(
                session, tenant_id, invoice_id, items
            )
            invoice.subtotal = subtotal
            invoice.tax = tax
            invoice.discount = discount
            invoice.total = total
            invoice.amount_due = total - invoice.amount_paid
            await session.commit()
            await session.refresh(invoice)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.INVOICE_UPDATED,
            source="finance",
            entity_type="invoice",
            entity_id=invoice.id,
            payload={"invoice_id": str(invoice.id)},
        )
        return invoice

    async def request_approval(self, tenant_id: uuid.UUID, invoice_id: uuid.UUID, actor_id: uuid.UUID | None) -> Invoice:
        """finance.request_invoice_approval's body. AUTO at the ToolRegistry
        layer; decides AUTO-approve vs. pending-approval itself using
        `invoice_policy.py` — see that module's docstring."""
        async with self._session_factory() as session:
            invoice = await session.get(Invoice, invoice_id)
            if invoice is None or invoice.tenant_id != tenant_id:
                raise InvoiceNotFoundError("Invoice not found")
            if invoice.status != InvoiceStatus.DRAFT:
                raise InvalidInvoiceTransitionError("Only DRAFT invoices can request approval")

            unresolved_scope_change = None
            if invoice.job_id:
                unresolved_scope_change = (
                    await session.execute(
                        select(ScopeChange).where(
                            ScopeChange.tenant_id == tenant_id,
                            ScopeChange.job_id == invoice.job_id,
                            ScopeChange.status == ScopeChangeStatus.PENDING_APPROVAL,
                        )
                    )
                ).scalar_one_or_none()

            decision = evaluate_invoice_approval(invoice, has_unresolved_scope_change=unresolved_scope_change is not None)

            approval_id = None
            if decision.requires_approval:
                invoice.status = InvoiceStatus.PENDING_APPROVAL
                approval_id = await create_approval_request(
                    session,
                    tenant_id=tenant_id,
                    requested_by_type="USER",
                    requested_by_id=actor_id,
                    tool_name="finance.approve_invoice",
                    action_type="invoice_approval",
                    reason="; ".join(decision.reasons),
                    tool_input={"invoice_id": str(invoice.id), "total": str(invoice.total)},
                )
            else:
                invoice.status = InvoiceStatus.APPROVED

            await session.commit()
            await session.refresh(invoice)

        event_type = EventType.INVOICE_APPROVAL_REQUESTED if decision.requires_approval else EventType.INVOICE_APPROVED
        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=event_type,
            source="finance",
            entity_type="invoice",
            entity_id=invoice.id,
            payload={"invoice_id": str(invoice.id), "approval_id": str(approval_id) if approval_id else None},
        )
        return invoice

    async def decide_approval(
        self, tenant_id: uuid.UUID, invoice_id: uuid.UUID, *, approved: bool, decided_by: uuid.UUID | None, note: str | None
    ) -> Invoice:
        from app.models.approval import ApprovalRequest, ApprovalStatus

        async with self._session_factory() as session:
            invoice = await session.get(Invoice, invoice_id)
            if invoice is None or invoice.tenant_id != tenant_id:
                raise InvoiceNotFoundError("Invoice not found")
            if invoice.status != InvoiceStatus.PENDING_APPROVAL:
                raise InvalidInvoiceTransitionError("Invoice is not pending approval")

            pending = (
                await session.execute(
                    select(ApprovalRequest).where(
                        ApprovalRequest.tenant_id == tenant_id,
                        ApprovalRequest.tool_name == "finance.approve_invoice",
                        ApprovalRequest.status == ApprovalStatus.PENDING,
                    )
                )
            ).scalars().all()
            for req in pending:
                if req.tool_input.get("invoice_id") == str(invoice_id):
                    req.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
                    req.decided_by = decided_by
                    req.decision_note = note

            invoice.status = InvoiceStatus.APPROVED if approved else InvoiceStatus.DRAFT
            await session.commit()
            await session.refresh(invoice)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.INVOICE_APPROVED if approved else EventType.INVOICE_REJECTED,
            source="finance",
            entity_type="invoice",
            entity_id=invoice.id,
            payload={"invoice_id": str(invoice.id)},
        )
        return invoice

    async def send_invoice(self, tenant_id: uuid.UUID, invoice_id: uuid.UUID, delivery_provider) -> Invoice:
        async with self._session_factory() as session:
            invoice = await session.get(Invoice, invoice_id)
            if invoice is None or invoice.tenant_id != tenant_id:
                raise InvoiceNotFoundError("Invoice not found")
            if invoice.status != InvoiceStatus.APPROVED:
                raise InvalidInvoiceTransitionError("Only APPROVED invoices can be sent")

            customer = await session.get(Customer, invoice.customer_id)

            invoice.status = InvoiceStatus.SENT
            invoice.sent_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(invoice)

        await delivery_provider.send_invoice(
            tenant_id, invoice_id=invoice.id, customer_email=customer.email if customer else None,
            amount=invoice.total, invoice_number=invoice.invoice_number,
        )

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.INVOICE_SENT,
            source="finance",
            entity_type="invoice",
            entity_id=invoice.id,
            payload={"invoice_id": str(invoice.id)},
        )
        return invoice

    async def void_invoice(self, tenant_id: uuid.UUID, invoice_id: uuid.UUID, reason: str) -> Invoice:
        async with self._session_factory() as session:
            invoice = await session.get(Invoice, invoice_id)
            if invoice is None or invoice.tenant_id != tenant_id:
                raise InvoiceNotFoundError("Invoice not found")
            if invoice.status in (InvoiceStatus.PAID, InvoiceStatus.VOID, InvoiceStatus.CANCELLED):
                raise InvalidInvoiceTransitionError(f"Cannot void an invoice with status {invoice.status}")

            invoice.status = InvoiceStatus.VOID
            invoice.voided_at = datetime.now(timezone.utc)
            invoice.notes = f"{invoice.notes or ''}\nVoided: {reason}".strip()
            await session.commit()
            await session.refresh(invoice)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.INVOICE_VOIDED,
            source="finance",
            entity_type="invoice",
            entity_id=invoice.id,
            payload={"invoice_id": str(invoice.id), "reason": reason},
        )
        return invoice
