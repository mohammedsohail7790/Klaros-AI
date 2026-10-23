"""Dropshipping store domain (Phase A, branch 1: schema only).

A vertical EXTENSION in the sense of KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md: new tables that reference core entities (`customers`, `invoices`) by key and never
duplicate them. Nothing here changes an existing table. `Vendor`/`VendorBill` are deliberately NOT reused for suppliers (they have no product concept).

Eight tenant-scoped tables:

  Supplier           who fulfils: MANUAL today (staff place the order by hand), CSV/API later.
  Product            a catalog entry. A food product must be marked non-perishable to be published (enforced by the service, branch 2).
  Sku                the sellable variant: price, supplier cost (staff-only), availability (AVAILABLE / OUT_OF_STOCK / UNKNOWN, default UNKNOWN).
  InventorySnapshot  APPEND-ONLY stock observations (quantity may be unknown). Phase B fills it from the supplier.
  Order              one customer order. Money is Numeric(14,2) like core finance. `tax` is recorded explicitly and is 0 in Phase A (sales tax is not collected).
                     Personal data (contact_email, contact_phone, ship_to) is cleared on erasure.
  OrderLine          APPEND-ONLY snapshot of what was bought (name and unit price at purchase), never edited.
  SupplierOrder      the order as placed with a supplier: status, the supplier's own reference, who placed it and when.
  Shipment           tracking: carrier, number, URL, status.

Returns (`order_returns`) are deliberately deferred.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class SupplierIntegration(StrEnum):
    MANUAL = "MANUAL"  # staff place each supplier order by hand
    CSV = "CSV"
    API = "API"


class SupplierStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class ProductStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class SkuAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    UNKNOWN = "UNKNOWN"  # the safe default: nothing is claimed about stock that nobody has confirmed


class SkuStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class InventorySource(StrEnum):
    MANUAL = "MANUAL"
    FEED = "FEED"
    API = "API"


class OrderStatus(StrEnum):
    PENDING_PAYMENT = "PENDING_PAYMENT"
    PAID = "PAID"
    SUPPLIER_ORDER_PLACED = "SUPPLIER_ORDER_PLACED"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"
    REFUND_REQUESTED = "REFUND_REQUESTED"


class SupplierOrderStatus(StrEnum):
    TO_PLACE = "TO_PLACE"
    PLACED = "PLACED"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ShipmentStatus(StrEnum):
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    EXCEPTION = "EXCEPTION"


class Supplier(TenantScopedMixin, Base):
    __tablename__ = "dropshipping_suppliers"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_ds_suppliers_tenant_name"),)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    integration_type: Mapped[str] = mapped_column(String(12), nullable=False, default=SupplierIntegration.MANUAL, server_default="MANUAL")
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default=SupplierStatus.ACTIVE, server_default="ACTIVE")
    # Daily stock and price sync from the supplier's CSV feed (added by 0083). The feed never creates products: it updates stock, cost and, with a markup, price.
    feed_url: Mapped[str | None] = mapped_column(String(500), nullable=True)  # https only
    markup_percent: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)  # price = cost x (1 + markup/100); None = the sync never touches prices
    stock_buffer: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")  # a quantity at or below this counts as sold out
    max_price_change_percent: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False, default=Decimal("30"), server_default="30")  # a bigger move is held for review
    sync_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Product(TenantScopedMixin, Base):
    __tablename__ = "dropshipping_products"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_ds_products_tenant_slug"),
        Index("ix_ds_products_tenant_status", "tenant_id", "status"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)  # https only, validated by the service
    is_non_perishable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    status: Mapped[str] = mapped_column(String(12), nullable=False, default=ProductStatus.DRAFT, server_default="DRAFT")


class Sku(TenantScopedMixin, Base):
    __tablename__ = "dropshipping_skus"
    __table_args__ = (UniqueConstraint("tenant_id", "sku_code", name="uq_ds_skus_tenant_code"),)

    product_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("dropshipping_products.id"), nullable=False, index=True)
    supplier_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("dropshipping_suppliers.id"), nullable=False, index=True)
    sku_code: Mapped[str] = mapped_column(String(80), nullable=False)
    supplier_sku: Mapped[str | None] = mapped_column(String(120), nullable=True)
    price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    supplier_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)  # staff-only: never exposed publicly
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD", server_default="USD")
    weight_grams: Mapped[int | None] = mapped_column(Integer, nullable=True)
    availability: Mapped[str] = mapped_column(String(14), nullable=False, default=SkuAvailability.UNKNOWN, server_default="UNKNOWN")
    status: Mapped[str] = mapped_column(String(12), nullable=False, default=SkuStatus.ACTIVE, server_default="ACTIVE")


class InventorySnapshot(TenantScopedMixin, Base):
    __tablename__ = "dropshipping_inventory_snapshots"
    __table_args__ = (Index("ix_ds_inventory_sku_observed", "sku_id", "observed_at"),)

    sku_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("dropshipping_skus.id"), nullable=False)
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = unknown
    source: Mapped[str] = mapped_column(String(12), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Order(TenantScopedMixin, Base):
    __tablename__ = "dropshipping_orders"
    __table_args__ = (
        UniqueConstraint("tenant_id", "order_number", name="uq_ds_orders_tenant_number"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_ds_orders_tenant_idem"),
        UniqueConstraint("public_token", name="uq_ds_orders_public_token"),
        Index("ix_ds_orders_tenant_status", "tenant_id", "status"),
    )

    customer_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("customers.id"), nullable=True, index=True)
    order_number: Mapped[str] = mapped_column(String(32), nullable=False)
    public_token: Mapped[str] = mapped_column(String(64), nullable=False)  # unguessable: the shopper's order-status link
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)  # personal data: cleared on erasure
    contact_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)  # personal data: cleared on erasure
    ship_to: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # personal data: cleared on erasure
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD", server_default="USD")
    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    shipping: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0, server_default="0")
    tax: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0, server_default="0")  # Phase A: 0, recorded explicitly
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)  # the core invoice raised for this order
    stripe_session_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Added by 0081 (branch 3): how it was paid (the reference makes recording a payment idempotent), and the lifecycle facts.
    payment_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    payment_source: Mapped[str | None] = mapped_column(String(12), nullable=True)  # STRIPE | MANUAL
    status_note: Mapped[str | None] = mapped_column(Text, nullable=True)  # e.g. why it was cancelled, or that a refund is needed
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    personal_data_erased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Added by 0086: how many "your order is waiting for payment" e-mails this order has had (at most two), and when the last one went.
    payment_reminders_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_payment_reminder_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OrderLine(TenantScopedMixin, Base):
    __tablename__ = "dropshipping_order_lines"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_ds_order_lines_qty_positive"),)

    order_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("dropshipping_orders.id"), nullable=False, index=True)
    sku_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("dropshipping_skus.id"), nullable=False)
    name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)


class SupplierOrder(TenantScopedMixin, Base):
    __tablename__ = "dropshipping_supplier_orders"
    __table_args__ = (
        UniqueConstraint("tenant_id", "order_id", "supplier_id", name="uq_ds_supplier_orders_order_supplier"),
        Index("ix_ds_supplier_orders_tenant_status", "tenant_id", "status"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("dropshipping_orders.id"), nullable=False, index=True)
    supplier_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("dropshipping_suppliers.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    supplier_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    placed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    placed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class Shipment(TenantScopedMixin, Base):
    __tablename__ = "dropshipping_shipments"

    supplier_order_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("dropshipping_supplier_orders.id"), nullable=False, index=True)
    carrier: Mapped[str | None] = mapped_column(String(80), nullable=True)
    tracking_number: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tracking_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    entered_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)  # added by 0081: what went wrong, for an EXCEPTION


class ProblemStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ProblemReport(TenantScopedMixin, Base):
    """A shopper's report (made from their own order page) that something is wrong with what arrived. Staff approve it, which flags the order for a refund, or reject it with a note."""

    __tablename__ = "dropshipping_problem_reports"
    __table_args__ = (Index("ix_ds_problem_reports_tenant_status", "tenant_id", "status"),)

    order_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("dropshipping_orders.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default=ProblemStatus.PENDING.value, server_default="PENDING")
    description: Mapped[str] = mapped_column(Text, nullable=False)  # what the shopper wrote: personal data, shown to staff only
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
