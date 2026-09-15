import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.models.finance import Invoice, InvoiceLineItem
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/invoices", tags=["invoices"])


def _invoice_to_dict(inv: Invoice) -> dict[str, Any]:
    return {
        "id": str(inv.id),
        "invoice_number": inv.invoice_number,
        "customer_id": str(inv.customer_id),
        "job_id": str(inv.job_id) if inv.job_id else None,
        "status": inv.status,
        "issue_date": inv.issue_date.isoformat(),
        "due_date": inv.due_date.isoformat(),
        "currency": inv.currency,
        "subtotal": str(inv.subtotal),
        "tax": str(inv.tax),
        "discount": str(inv.discount),
        "total": str(inv.total),
        "amount_paid": str(inv.amount_paid),
        "amount_due": str(inv.amount_due),
        "notes": inv.notes,
        "sent_at": inv.sent_at.isoformat() if inv.sent_at else None,
        "paid_at": inv.paid_at.isoformat() if inv.paid_at else None,
        "voided_at": inv.voided_at.isoformat() if inv.voided_at else None,
    }


def _line_item_to_dict(li: InvoiceLineItem) -> dict[str, Any]:
    return {
        "id": str(li.id),
        "description": li.description,
        "quantity": str(li.quantity),
        "unit_price": str(li.unit_price),
        "discount": str(li.discount),
        "tax_rate": str(li.tax_rate),
        "line_total": str(li.line_total),
    }


@router.get("")
async def list_invoices(
    status: str | None = None,
    customer_id: uuid.UUID | None = None,
    job_id: uuid.UUID | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    query = select(Invoice).where(Invoice.tenant_id == current_user.tenant_id)
    if status:
        query = query.where(Invoice.status == status)
    if customer_id:
        query = query.where(Invoice.customer_id == customer_id)
    if job_id:
        query = query.where(Invoice.job_id == job_id)
    query = query.order_by(Invoice.issue_date.desc())
    rows = (await db.execute(query)).scalars().all()
    return {"invoices": [_invoice_to_dict(i) for i in rows]}


@router.get("/{invoice_id}")
async def get_invoice(
    invoice_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    invoice = await db.get(Invoice, invoice_id)
    if invoice is None or invoice.tenant_id != current_user.tenant_id:
        from fastapi import HTTPException, status

        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    items = (
        await db.execute(
            select(InvoiceLineItem)
            .where(InvoiceLineItem.tenant_id == current_user.tenant_id, InvoiceLineItem.invoice_id == invoice_id)
            .order_by(InvoiceLineItem.sort_order)
        )
    ).scalars().all()
    result = _invoice_to_dict(invoice)
    result["line_items"] = [_line_item_to_dict(i) for i in items]
    return result


class _ToolPost:
    """Thin helper so every action endpoint below is one line: call the
    named tool via the ToolRegistry with the given payload."""


async def _call_tool(tool_name: str, payload: dict, current_user: CurrentUser, registry: ToolRegistry) -> dict:
    try:
        output = await registry.execute(tool_name, payload, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


class LineItemRequest(BaseModel):
    description: str
    quantity: Decimal
    unit_price: Decimal
    discount: Decimal = Decimal("0")
    tax_rate: Decimal = Decimal("0")


class CreateInvoiceDraftRequest(BaseModel):
    customer_id: uuid.UUID
    job_id: uuid.UUID | None = None
    line_items: list[LineItemRequest]
    due_date: str | None = None


@router.post("", status_code=201)
async def create_invoice_draft(
    body: CreateInvoiceDraftRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _call_tool(
        "finance.create_invoice_draft", body.model_dump(mode="json"), current_user, registry
    )


class UpdateInvoiceDraftRequest(BaseModel):
    line_items: list[LineItemRequest]


@router.patch("/{invoice_id}")
async def update_invoice_draft(
    invoice_id: uuid.UUID,
    body: UpdateInvoiceDraftRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload = {"invoice_id": str(invoice_id), **body.model_dump(mode="json")}
    return await _call_tool("finance.update_invoice_draft", payload, current_user, registry)


@router.post("/trigger-from-job")
async def trigger_invoice_from_job(
    job_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _call_tool("finance.trigger_invoice_from_job", {"job_id": str(job_id)}, current_user, registry)


class BulkImportInvoiceRowRequest(BaseModel):
    customer_name: str
    customer_email: str | None = None
    customer_phone: str | None = None
    invoice_number: str | None = None
    issue_date: date
    due_date: date
    amount: Decimal
    amount_paid: Decimal = Decimal("0")
    description: str | None = None


@router.post("/import", status_code=201)
async def bulk_import_invoices(
    body: list[BulkImportInvoiceRowRequest],
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _call_tool(
        "finance.bulk_import_invoices",
        {"invoices": [row.model_dump(mode="json") for row in body]},
        current_user,
        registry,
    )


@router.post("/{invoice_id}/request-approval")
async def request_invoice_approval(
    invoice_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _call_tool(
        "finance.request_invoice_approval", {"invoice_id": str(invoice_id)}, current_user, registry
    )


@router.post("/{invoice_id}/approve")
async def approve_invoice(
    invoice_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _call_tool("finance.approve_invoice", {"invoice_id": str(invoice_id)}, current_user, registry)


@router.post("/{invoice_id}/reject")
async def reject_invoice(
    invoice_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _call_tool("finance.reject_invoice", {"invoice_id": str(invoice_id)}, current_user, registry)


@router.post("/{invoice_id}/send")
async def send_invoice(
    invoice_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _call_tool("finance.send_invoice", {"invoice_id": str(invoice_id)}, current_user, registry)


@router.post("/{invoice_id}/checkout")
async def create_invoice_checkout(
    invoice_id: uuid.UUID,
    body: dict,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _call_tool(
        "finance.create_stripe_checkout_session",
        {**body, "invoice_id": str(invoice_id)},
        current_user,
        registry,
    )


@router.post("/{invoice_id}/sync-to-quickbooks")
async def sync_invoice_to_quickbooks(
    invoice_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _call_tool(
        "finance.sync_invoice_to_quickbooks", {"invoice_id": str(invoice_id)}, current_user, registry
    )


@router.post("/{invoice_id}/void")
async def void_invoice(
    invoice_id: uuid.UUID,
    reason: str,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _call_tool(
        "finance.void_invoice", {"invoice_id": str(invoice_id), "reason": reason}, current_user, registry
    )
