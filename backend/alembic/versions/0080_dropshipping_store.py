"""Dropshipping store (Phase A): suppliers, products, SKUs, inventory snapshots, orders, order lines, supplier orders, shipments.

Additive only: nothing existing is dropped or rewritten. The only change to existing data is promoting the `dropshipping` row of `vertical_extensions` from
BETA/registry-only to ACTIVE 1.0.0 with its capability keys, taken from app/data/vertical_extension_seed.py (the single source of truth, as 0049 did for
medical_tourism). Every new table is tenant-scoped with row-level security on PostgreSQL (policies as in 0066). inventory_snapshots and order_lines are
append-only: no UPDATE policy.

Rollback: `alembic downgrade 0079_catalog_real_connectors` drops the eight tables (orders, catalog and tracking entered are lost: export them first if they matter)
and sets the vertical row back to its registry-only values.

Revision ID: 0080_dropshipping_store
Revises: 0079_catalog_real_connectors
Create Date: 2026-10-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0080_dropshipping_store"
down_revision: Union[str, None] = "0079_catalog_real_connectors"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = sa.Uuid(as_uuid=True)
_MONEY = sa.Numeric(14, 2)
_TABLES = (  # creation order, with the row-level-security verbs each table gets
    ("dropshipping_suppliers", ("select", "insert", "update")),
    ("dropshipping_products", ("select", "insert", "update")),
    ("dropshipping_skus", ("select", "insert", "update")),
    ("dropshipping_inventory_snapshots", ("select", "insert")),  # append-only
    ("dropshipping_orders", ("select", "insert", "update")),
    ("dropshipping_order_lines", ("select", "insert")),  # a line is a snapshot of what was bought: never edited
    ("dropshipping_supplier_orders", ("select", "insert", "update")),
    ("dropshipping_shipments", ("select", "insert", "update")),
)


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
    ]


def _vertical_table() -> sa.TableClause:
    return sa.table(
        "vertical_extensions",
        sa.column("id", _UUID),
        sa.column("version", sa.String),
        sa.column("status", sa.String),
        sa.column("capabilities", sa.JSON),
        sa.column("description", sa.Text),
    )


def upgrade() -> None:
    op.create_table(
        "dropshipping_suppliers", *_base(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("integration_type", sa.String(12), nullable=False, server_default="MANUAL"),
        sa.Column("contact_email", sa.String(255), nullable=True),
        sa.Column("contact_phone", sa.String(50), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="ACTIVE"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_ds_suppliers_tenant_name"),
    )

    op.create_table(
        "dropshipping_products", *_base(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(120), nullable=True),
        sa.Column("image_url", sa.String(500), nullable=True),
        sa.Column("is_non_perishable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(12), nullable=False, server_default="DRAFT"),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_ds_products_tenant_slug"),
    )
    op.create_index("ix_ds_products_tenant_status", "dropshipping_products", ["tenant_id", "status"])

    op.create_table(
        "dropshipping_skus", *_base(),
        sa.Column("product_id", _UUID, sa.ForeignKey("dropshipping_products.id"), nullable=False),
        sa.Column("supplier_id", _UUID, sa.ForeignKey("dropshipping_suppliers.id"), nullable=False),
        sa.Column("sku_code", sa.String(80), nullable=False),
        sa.Column("supplier_sku", sa.String(120), nullable=True),
        sa.Column("price", _MONEY, nullable=False),
        sa.Column("supplier_cost", _MONEY, nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("weight_grams", sa.Integer(), nullable=True),
        sa.Column("availability", sa.String(14), nullable=False, server_default="UNKNOWN"),
        sa.Column("status", sa.String(12), nullable=False, server_default="ACTIVE"),
        sa.UniqueConstraint("tenant_id", "sku_code", name="uq_ds_skus_tenant_code"),
    )
    op.create_index("ix_dropshipping_skus_product_id", "dropshipping_skus", ["product_id"])
    op.create_index("ix_dropshipping_skus_supplier_id", "dropshipping_skus", ["supplier_id"])

    op.create_table(
        "dropshipping_inventory_snapshots", *_base(),
        sa.Column("sku_id", _UUID, sa.ForeignKey("dropshipping_skus.id"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(12), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ds_inventory_sku_observed", "dropshipping_inventory_snapshots", ["sku_id", "observed_at"])

    op.create_table(
        "dropshipping_orders", *_base(),
        sa.Column("customer_id", _UUID, sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("order_number", sa.String(32), nullable=False),
        sa.Column("public_token", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("contact_email", sa.String(255), nullable=True),
        sa.Column("contact_phone", sa.String(50), nullable=True),
        sa.Column("ship_to", sa.JSON(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("subtotal", _MONEY, nullable=False),
        sa.Column("shipping", _MONEY, nullable=False, server_default="0"),
        sa.Column("tax", _MONEY, nullable=False, server_default="0"),
        sa.Column("total", _MONEY, nullable=False),
        sa.Column("invoice_id", _UUID, nullable=True),
        sa.Column("stripe_session_id", sa.String(120), nullable=True),
        sa.Column("stripe_payment_intent_id", sa.String(120), nullable=True),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "order_number", name="uq_ds_orders_tenant_number"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_ds_orders_tenant_idem"),
        sa.UniqueConstraint("public_token", name="uq_ds_orders_public_token"),
    )
    op.create_index("ix_ds_orders_tenant_status", "dropshipping_orders", ["tenant_id", "status"])
    op.create_index("ix_dropshipping_orders_customer_id", "dropshipping_orders", ["customer_id"])

    op.create_table(
        "dropshipping_order_lines", *_base(),
        sa.Column("order_id", _UUID, sa.ForeignKey("dropshipping_orders.id"), nullable=False),
        sa.Column("sku_id", _UUID, sa.ForeignKey("dropshipping_skus.id"), nullable=False),
        sa.Column("name_snapshot", sa.String(255), nullable=False),
        sa.Column("unit_price", _MONEY, nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("line_total", _MONEY, nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_ds_order_lines_qty_positive"),
    )
    op.create_index("ix_dropshipping_order_lines_order_id", "dropshipping_order_lines", ["order_id"])

    op.create_table(
        "dropshipping_supplier_orders", *_base(),
        sa.Column("order_id", _UUID, sa.ForeignKey("dropshipping_orders.id"), nullable=False),
        sa.Column("supplier_id", _UUID, sa.ForeignKey("dropshipping_suppliers.id"), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("supplier_reference", sa.String(160), nullable=True),
        sa.Column("placed_by", _UUID, nullable=True),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.UniqueConstraint("tenant_id", "order_id", "supplier_id", name="uq_ds_supplier_orders_order_supplier"),
    )
    op.create_index("ix_dropshipping_supplier_orders_order_id", "dropshipping_supplier_orders", ["order_id"])
    op.create_index("ix_dropshipping_supplier_orders_supplier_id", "dropshipping_supplier_orders", ["supplier_id"])
    op.create_index("ix_ds_supplier_orders_tenant_status", "dropshipping_supplier_orders", ["tenant_id", "status"])

    op.create_table(
        "dropshipping_shipments", *_base(),
        sa.Column("supplier_order_id", _UUID, sa.ForeignKey("dropshipping_supplier_orders.id"), nullable=False),
        sa.Column("carrier", sa.String(80), nullable=True),
        sa.Column("tracking_number", sa.String(120), nullable=True),
        sa.Column("tracking_url", sa.String(500), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entered_by", _UUID, nullable=True),
    )
    op.create_index("ix_dropshipping_shipments_supplier_order_id", "dropshipping_shipments", ["supplier_order_id"])

    # tenant_id is indexed on every table, like every TenantScopedMixin table (the row-level-security policies filter on it).
    for table, _verbs in _TABLES:
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])

    # Promote the registry row. Single source of truth: app/data/vertical_extension_seed.py (imported, never retyped, so the migration and the tests cannot drift).
    from app.data.vertical_extension_seed import DROPSHIPPING_ID, SEED_VERTICALS

    promoted = next(v for v in SEED_VERTICALS if v["id"] == DROPSHIPPING_ID)
    vertical = _vertical_table()
    op.execute(
        vertical.update()
        .where(vertical.c.id == DROPSHIPPING_ID)
        .values(
            version=promoted["version"],
            status=str(promoted["status"]),
            capabilities=promoted["capabilities"],
            description=promoted["description"],
        )
    )

    if op.get_bind().dialect.name != "postgresql":
        return  # row-level security is PostgreSQL-only, as in 0066
    for table, verbs in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        for verb in verbs:
            if verb == "select":
                op.execute(f"CREATE POLICY tenant_select ON {table} FOR SELECT USING (tenant_id = current_tenant_id())")
            elif verb == "insert":
                op.execute(f"CREATE POLICY tenant_insert ON {table} FOR INSERT WITH CHECK (tenant_id = current_tenant_id())")
            else:
                op.execute(f"CREATE POLICY tenant_update ON {table} FOR UPDATE USING (tenant_id = current_tenant_id()) WITH CHECK (tenant_id = current_tenant_id())")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table, _verbs in _TABLES:
            for policy in ("tenant_select", "tenant_insert", "tenant_update"):
                op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
    for table, _verbs in reversed(_TABLES):
        op.drop_table(table)

    from app.data.vertical_extension_seed import DROPSHIPPING_ID

    vertical = _vertical_table()
    op.execute(
        vertical.update()
        .where(vertical.c.id == DROPSHIPPING_ID)
        .values(
            version="0.0.0-registry-only",
            status="BETA",
            capabilities=[],
            description=(
                "Supplier/product/order/fulfillment domain. Table family ships in a later "
                "phase; this row exists so the registry mechanism can be exercised now."
            ),
        )
    )
