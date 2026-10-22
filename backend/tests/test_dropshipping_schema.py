"""Dropshipping store, branch 1: the schema and the registry promotion.

Covers (a) the models and their database rules (defaults, uniqueness, the quantity check), (b) the vertical registry row now being ACTIVE with real capability
keys while medical_tourism is untouched, and (c) the real Alembic chain: upgrade to head creates the eight tables and promotes the row, downgrade removes the
tables and restores the registry-only row, and the chain still has exactly one head. `tests/conftest.py::_reset_database` builds the schema from
`Base.metadata`, so (c) runs the actual migrations against a disposable SQLite file, like tests/test_migration_schema_matches_models.py.
"""

import os
import sqlite3
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.data.vertical_extension_seed import DROPSHIPPING_ID, MEDICAL_TOURISM_ID, SEED_VERTICALS
from app.db.session import async_session_maker
from app.models.dropshipping import (
    InventorySnapshot,
    Order,
    OrderLine,
    OrderStatus,
    Product,
    ProductStatus,
    Shipment,
    ShipmentStatus,
    Sku,
    SkuAvailability,
    Supplier,
    SupplierIntegration,
    SupplierOrder,
    SupplierOrderStatus,
)
from app.models.vertical_extension import VerticalExtensionStatus

BACKEND_ROOT = Path(__file__).resolve().parents[1]
NEW_TABLES = {
    "dropshipping_suppliers", "dropshipping_products", "dropshipping_skus", "dropshipping_inventory_snapshots",
    "dropshipping_orders", "dropshipping_order_lines", "dropshipping_supplier_orders", "dropshipping_shipments",
}


def _now() -> datetime:
    return datetime.now(UTC)


async def _make_catalog(session, tenant_id: uuid.UUID, *, sku_code: str = "FICT-1001", slug: str = "fictional-lamp"):
    supplier = Supplier(tenant_id=tenant_id, name=f"Fictional Supplier {uuid.uuid4().hex[:6]}")
    product = Product(tenant_id=tenant_id, name="FICTIONAL demo lamp", slug=slug)
    session.add_all([supplier, product])
    await session.flush()
    sku = Sku(tenant_id=tenant_id, product_id=product.id, supplier_id=supplier.id, sku_code=sku_code, price=Decimal("34.00"))
    session.add(sku)
    await session.flush()
    return supplier, product, sku


def _order(tenant_id: uuid.UUID, **over) -> Order:
    base = dict(
        tenant_id=tenant_id, order_number=f"DS-{uuid.uuid4().hex[:8]}", public_token=uuid.uuid4().hex, status=OrderStatus.PENDING_PAYMENT,
        subtotal=Decimal("34.00"), total=Decimal("34.00"), idempotency_key=uuid.uuid4().hex, placed_at=_now(),
    )
    base.update(over)
    return Order(**base)


# --- the models and their database rules -----------------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_whole_chain_can_be_written_and_read_back_with_safe_defaults() -> None:
    tenant = uuid.uuid4()
    async with async_session_maker() as session:
        supplier, product, sku = await _make_catalog(session, tenant)
        order = _order(tenant)
        session.add(order)
        await session.flush()
        line = OrderLine(tenant_id=tenant, order_id=order.id, sku_id=sku.id, name_snapshot=product.name, unit_price=sku.price, quantity=2, line_total=Decimal("68.00"))
        supplier_order = SupplierOrder(tenant_id=tenant, order_id=order.id, supplier_id=supplier.id, status=SupplierOrderStatus.TO_PLACE)
        session.add_all([line, supplier_order, InventorySnapshot(tenant_id=tenant, sku_id=sku.id, quantity=None, source="MANUAL", observed_at=_now())])
        await session.flush()
        shipment = Shipment(tenant_id=tenant, supplier_order_id=supplier_order.id, status=ShipmentStatus.SHIPPED, carrier="FictionalPost", tracking_number="FICT123")
        session.add(shipment)
        await session.commit()

        await session.refresh(supplier)
        await session.refresh(product)
        await session.refresh(sku)
        await session.refresh(order)
        # nothing is claimed that nobody confirmed: stock unknown, product unpublished and not marked non-perishable, supplier manual, no tax collected
        assert supplier.integration_type == SupplierIntegration.MANUAL
        assert product.status == ProductStatus.DRAFT
        assert product.is_non_perishable is False
        assert sku.availability == SkuAvailability.UNKNOWN
        assert order.tax == Decimal("0.00") or order.tax == 0
        assert order.shipping == Decimal("0.00") or order.shipping == 0
        assert order.currency == "USD" and sku.currency == "USD"


@pytest.mark.asyncio
async def test_a_sku_code_is_unique_within_a_business_but_two_businesses_may_use_the_same_code() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    async with async_session_maker() as session:
        await _make_catalog(session, a, sku_code="SAME-1", slug="p-a")
        await _make_catalog(session, b, sku_code="SAME-1", slug="p-b")  # another business: allowed
        await session.commit()
    async with async_session_maker() as session:
        _, product, sku = await _make_catalog(session, a, sku_code="UNIQ-1", slug="p-a2")
        session.add(Sku(tenant_id=a, product_id=product.id, supplier_id=sku.supplier_id, sku_code="UNIQ-1", price=Decimal("1.00")))
        with pytest.raises(IntegrityError):
            await session.flush()


@pytest.mark.asyncio
async def test_order_number_and_idempotency_key_are_unique_per_business_and_the_public_token_is_unique_everywhere() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    async with async_session_maker() as session:
        first = _order(a, order_number="DS-1", idempotency_key="k1", public_token="t-1")
        session.add(first)
        session.add(_order(b, order_number="DS-1", idempotency_key="k1", public_token="t-2"))  # same number and key in another business: allowed
        await session.commit()
    async with async_session_maker() as session:
        session.add(_order(a, order_number="DS-1", idempotency_key="other", public_token="t-3"))
        with pytest.raises(IntegrityError):
            await session.flush()
    async with async_session_maker() as session:
        session.add(_order(a, order_number="DS-9", idempotency_key="k1", public_token="t-4"))  # a retried checkout must not create a second order
        with pytest.raises(IntegrityError):
            await session.flush()
    async with async_session_maker() as session:
        session.add(_order(b, order_number="DS-7", idempotency_key="k7", public_token="t-1"))  # a status link is unguessable AND unique across all businesses
        with pytest.raises(IntegrityError):
            await session.flush()


@pytest.mark.asyncio
async def test_an_order_line_must_have_a_positive_quantity() -> None:
    tenant = uuid.uuid4()
    async with async_session_maker() as session:
        _, product, sku = await _make_catalog(session, tenant)
        order = _order(tenant)
        session.add(order)
        await session.flush()
        session.add(OrderLine(tenant_id=tenant, order_id=order.id, sku_id=sku.id, name_snapshot=product.name, unit_price=sku.price, quantity=0, line_total=Decimal("0.00")))
        with pytest.raises(IntegrityError):
            await session.flush()


@pytest.mark.asyncio
async def test_one_supplier_order_per_order_and_supplier() -> None:
    tenant = uuid.uuid4()
    async with async_session_maker() as session:
        supplier, _, _ = await _make_catalog(session, tenant)
        order = _order(tenant)
        session.add(order)
        await session.flush()
        session.add(SupplierOrder(tenant_id=tenant, order_id=order.id, supplier_id=supplier.id, status=SupplierOrderStatus.TO_PLACE))
        await session.flush()
        session.add(SupplierOrder(tenant_id=tenant, order_id=order.id, supplier_id=supplier.id, status=SupplierOrderStatus.TO_PLACE))
        with pytest.raises(IntegrityError):
            await session.flush()


# --- the registry ---------------------------------------------------------------------------------------------------------------------------------------


def test_dropshipping_is_promoted_in_the_registry_seed_and_medical_tourism_is_untouched() -> None:
    by_key = {v["key"]: v for v in SEED_VERTICALS}
    drop = by_key["dropshipping"]
    assert drop["id"] == DROPSHIPPING_ID
    assert drop["status"] == VerticalExtensionStatus.ACTIVE
    assert drop["version"] == "1.0.0"
    caps = drop["capabilities"]
    assert caps and all(c.startswith("dropshipping.") for c in caps)
    for needed in ("product_catalog", "supplier_connection", "inventory", "pricing", "order_management", "fulfilment"):
        assert f"dropshipping.{needed}" in caps
    mt = by_key["medical_tourism"]
    assert mt["id"] == MEDICAL_TOURISM_ID and mt["status"] == VerticalExtensionStatus.ACTIVE and mt["version"] == "1.0.0"
    assert mt["capabilities"] == [
        "medical_tourism.provider_directory", "medical_tourism.procedure_catalog", "medical_tourism.provider_offerings",
        "medical_tourism.compliance_verification", "medical_tourism.patient_leads", "medical_tourism.cross_border_commission", "medical_tourism.multi_currency",
    ]


# --- the real migration chain ----------------------------------------------------------------------------------------------------------------------------


def _alembic(db_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}", "DATABASE_MIGRATION_URL": f"sqlite+aiosqlite:///{db_path}"}
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND_ROOT, env=env, capture_output=True, text=True, timeout=180)


def _tables(db_path: Path) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def _drop_row(db_path: Path) -> tuple:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT version, status, capabilities FROM vertical_extensions WHERE key = 'dropshipping'").fetchone()
    finally:
        conn.close()


def test_the_migration_chain_has_exactly_one_head() -> None:
    result = subprocess.run([sys.executable, "-m", "alembic", "heads"], cwd=BACKEND_ROOT, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    heads = [line for line in result.stdout.splitlines() if "(head)" in line]
    assert len(heads) == 1, result.stdout  # later migrations may extend the chain; it must never fork


def _columns(db_path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def test_0081_adds_the_order_columns_and_downgrade_removes_only_them(tmp_path) -> None:
    db = tmp_path / "ds_0081.db"
    assert _alembic(db, "upgrade", "head").returncode == 0
    order_cols = {"payment_reference", "payment_source", "status_note", "cancelled_at", "delivered_at", "personal_data_erased_at"}
    assert order_cols <= _columns(db, "dropshipping_orders") and "note" in _columns(db, "dropshipping_shipments")
    down = _alembic(db, "downgrade", "0080_dropshipping_store")
    assert down.returncode == 0, f"{down.stdout}\n{down.stderr}"
    assert not (order_cols & _columns(db, "dropshipping_orders")) and "note" not in _columns(db, "dropshipping_shipments")
    assert {"order_number", "public_token", "ship_to", "total"} <= _columns(db, "dropshipping_orders")  # the 0080 columns are untouched
    assert _alembic(db, "upgrade", "head").returncode == 0  # repeatable after a rollback


def test_upgrade_creates_the_tables_and_promotes_the_row_and_downgrade_restores_it(tmp_path) -> None:
    db = tmp_path / "ds_migration.db"
    up = _alembic(db, "upgrade", "head")
    assert up.returncode == 0, f"{up.stdout}\n{up.stderr}"
    assert NEW_TABLES <= _tables(db)
    version, status, capabilities = _drop_row(db)
    assert (version, status) == ("1.0.0", "ACTIVE")
    assert "dropshipping.order_management" in capabilities

    down = _alembic(db, "downgrade", "0079_catalog_real_connectors")
    assert down.returncode == 0, f"{down.stdout}\n{down.stderr}"
    assert not (NEW_TABLES & _tables(db))
    version, status, capabilities = _drop_row(db)
    assert (version, status) == ("0.0.0-registry-only", "BETA")
    assert capabilities in ("[]", [])

    again = _alembic(db, "upgrade", "head")  # the upgrade is repeatable after a rollback
    assert again.returncode == 0, f"{again.stdout}\n{again.stderr}"
    assert NEW_TABLES <= _tables(db)


def test_0083_adds_the_supplier_sync_columns_and_downgrade_removes_only_them(tmp_path) -> None:
    db = tmp_path / "ds_0083.db"
    assert _alembic(db, "upgrade", "head").returncode == 0
    sync_cols = {"feed_url", "markup_percent", "stock_buffer", "max_price_change_percent", "sync_enabled", "last_sync_at", "last_sync_summary"}
    assert sync_cols <= _columns(db, "dropshipping_suppliers")
    down = _alembic(db, "downgrade", "0082_merge_outreach_dropshipping")
    assert down.returncode == 0, f"{down.stdout}\n{down.stderr}"
    assert not (sync_cols & _columns(db, "dropshipping_suppliers")) and {"name", "integration_type", "status"} <= _columns(db, "dropshipping_suppliers")
    assert "payment_reference" in _columns(db, "dropshipping_orders")  # 0081 and the merge are untouched
    assert _alembic(db, "upgrade", "head").returncode == 0  # repeatable after a rollback
