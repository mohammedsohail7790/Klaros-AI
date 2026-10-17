import type { DsOrder, DsProduct, StoreView, WorkQueueEntry } from "@/lib/dropshippingApi";

export const SHIP = { name: "Pat Shopper", line1: "12 Main St", line2: null, city: "Austin", state: "TX", postal_code: "78701" };

export const order = (over: Partial<DsOrder> = {}): DsOrder => ({
  id: "o1", order_number: "DS-ABCD2345", status: "PAID", currency: "USD", subtotal: "30.00", shipping: "5.00", tax: "0.00", total: "35.00",
  placed_at: "2026-10-16T10:00:00Z", paid_at: "2026-10-16T10:05:00Z", delivered_at: null, cancelled_at: null, payment_source: "STRIPE", status_note: null,
  personal_data_erased: false,
  lines: [{ sku_id: "k1", sku_code: "TEA-1", name: "Green Tea", unit_price: "10.00", quantity: 2, line_total: "20.00" }, { sku_id: "k2", sku_code: "RICE-1", name: "Brown Rice", unit_price: "10.00", quantity: 1, line_total: "10.00" }],
  supplier_orders: [{ id: "s1", supplier: "Acme Supply", status: "TO_PLACE", supplier_reference: null, note: null, shipments: [] }],
  contact_email: "pat@example.com", contact_phone: "512-555-0147", ship_to: SHIP, payment_reference: "pi_123", customer_id: "c1",
  ...over,
});

export const work = (over: Partial<WorkQueueEntry> = {}): WorkQueueEntry => ({
  id: "s1", order_id: "o1", order_number: "DS-ABCD2345", order_status: "PAID", status: "TO_PLACE", supplier: "Acme Supply", supplier_reference: null, note: null,
  items: [{ supplier_sku: "TEA-1-S", sku_code: "TEA-1", name: "Green Tea", quantity: 2 }], ship_to: SHIP, contact_name: "Pat Shopper", shipments: [], ...over,
});

export const product = (over: Partial<DsProduct> = {}): DsProduct => ({
  id: "p1", slug: "green-tea", name: "Green Tea", description: null, category: "Tea", image_url: null, is_non_perishable: true, status: "DRAFT",
  skus: [{ id: "k1", sku_code: "TEA-1", supplier: "Acme Supply", supplier_sku: "TEA-1-S", price: "10.00", supplier_cost: "6.00", margin: "4.00", currency: "USD", weight_grams: null, availability: "AVAILABLE", status: "ACTIVE" }],
  publish_problems: [], ...over,
});

export const storeView = (over: Partial<StoreView> = {}, settings: Partial<StoreView["settings"]> = {}): StoreView => ({
  settings: { open: false, shipping_flat_fee: null, free_shipping_over: null, sales_tax_acknowledged: false, ...settings },
  open_to_shoppers: false, blockers: [], ...over,
});
