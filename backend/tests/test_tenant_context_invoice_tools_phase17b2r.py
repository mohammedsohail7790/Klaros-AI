"""Phase 17B-2R: real-PostgreSQL behavioral proof that the 2
independently-opened sessions in app/tools/builtin/invoice_tools.py
(BulkImportInvoices._resolve_customer_id, GetInvoice) now stamp `SET
LOCAL app.tenant_id`."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.actor import ActorType
from app.models.crm import Customer
from app.models.finance import Invoice, InvoiceStatus
from app.models.rbac import Role
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)


def _ctx(tenant_id, role=Role.OWNER, actor_type=ActorType.USER):
    return ExecutionContext(tenant_id=tenant_id, actor_type=actor_type, actor_id=uuid.uuid4(), role=role)


class _ContextSpy:
    def __init__(self):
        self.calls: list[tuple[uuid.UUID | None, str | None]] = []

    async def __call__(self, session, tenant_id):
        await set_tenant_context(session, tenant_id)
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            readback = await session.scalar(text("select current_setting('app.tenant_id', true)"))
        else:
            readback = None
        self.calls.append((tenant_id, readback))


@pytest.fixture
def spy():
    return _ContextSpy()


async def _make_invoice(tenant_id: uuid.UUID) -> Invoice:
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Invoice Tools Test Customer")
        session.add(customer)
        await session.flush()
        invoice = Invoice(
            tenant_id=tenant_id, customer_id=customer.id, invoice_number=f"ITOOL-{uuid.uuid4().hex[:8]}",
            status=InvoiceStatus.DRAFT, issue_date=date(2026, 1, 1), due_date=date(2026, 2, 1),
            subtotal=Decimal("100.00"), total=Decimal("100.00"), amount_due=Decimal("100.00"),
        )
        session.add(invoice)
        await session.commit()
        await session.refresh(invoice)
        return invoice


@requires_real_postgres
async def test_bulk_import_and_get_invoice_set_tenant_context(monkeypatch, spy, tool_registry) -> None:
    import app.tools.builtin.invoice_tools as invoice_tools_module

    monkeypatch.setattr(invoice_tools_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    result = await tool_registry.execute(
        "finance.bulk_import_invoices",
        {
            "invoices": [
                {
                    "customer_name": "Imported Customer",
                    "issue_date": "2026-01-01",
                    "due_date": "2026-02-01",
                    "amount": "250.00",
                }
            ]
        },
        ctx,
    )
    assert result.created_count == 1
    invoice_id = result.results[0].invoice_id

    fetched = await tool_registry.execute("finance.get_invoice", {"invoice_id": invoice_id}, ctx)
    assert fetched.invoice["id"] == invoice_id

    assert len(spy.calls) >= 2
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_invoice_never_fetchable_by_tenant_b(tool_registry) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    invoice_a = await _make_invoice(tenant_a)

    with pytest.raises(ValueError, match="not found"):
        await tool_registry.execute("finance.get_invoice", {"invoice_id": str(invoice_a.id)}, _ctx(tenant_b))
