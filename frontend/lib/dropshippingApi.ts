// Staff API for the dropshipping store (/api/v1/dropshipping/*). Kept out of lib/api.ts, which is already very large.
// Money and statuses are always the server's own strings: the browser never recomputes a total or a price.
import { ApiError, authHeaders, request } from "@/lib/api";

const BASE = "/api/v1/dropshipping";

export type OrderStatus = "PENDING_PAYMENT" | "PAID" | "SUPPLIER_ORDER_PLACED" | "SHIPPED" | "DELIVERED" | "CANCELLED" | "REFUND_REQUESTED";
export type SupplierOrderStatus = "TO_PLACE" | "PLACED" | "CONFIRMED" | "FAILED" | "CANCELLED";
export type Availability = "AVAILABLE" | "OUT_OF_STOCK" | "UNKNOWN";

export interface ShipTo { name: string; line1: string; line2?: string | null; city: string; state: string; postal_code: string }
export interface Shipment {
  id: string; carrier: string | null; tracking_number: string; tracking_url: string | null; status: string; note: string | null;
  shipped_at: string | null; delivered_at: string | null;
}
export interface OrderSupplierOrder {
  id: string; supplier: string | null; status: SupplierOrderStatus; supplier_reference: string | null; note: string | null; shipments: Shipment[];
}
export interface OrderLine { sku_id: string; sku_code: string | null; name: string; unit_price: string; quantity: number; line_total: string }
export interface DsOrder {
  id: string; order_number: string; status: OrderStatus; currency: string; subtotal: string; shipping: string; tax: string; total: string;
  placed_at: string | null; paid_at: string | null; delivered_at: string | null; cancelled_at: string | null;
  payment_source: string | null; status_note: string | null; personal_data_erased: boolean;
  lines: OrderLine[]; supplier_orders: OrderSupplierOrder[];
  contact_email?: string | null; contact_phone?: string | null; ship_to?: ShipTo | null; payment_reference?: string | null; customer_id?: string | null;
}
export interface WorkItem { supplier_sku: string | null; sku_code: string; name: string; quantity: number }
export interface WorkQueueEntry {
  id: string; order_id: string; order_number: string; order_status: OrderStatus; status: SupplierOrderStatus; supplier: string | null;
  supplier_reference: string | null; note: string | null; items: WorkItem[]; ship_to: ShipTo | null; contact_name: string | null;
  shipments: { id: string; carrier: string | null; tracking_number: string; status: string }[];
}
export interface DsSku {
  id: string; sku_code: string; supplier: string | null; supplier_sku: string | null; price: string; supplier_cost: string | null; margin: string | null;
  currency: string; weight_grams: number | null; availability: Availability; status: string;
}
export interface DsProduct {
  id: string; slug: string; name: string; description: string | null; category: string | null; image_url: string | null;
  is_non_perishable: boolean; status: "DRAFT" | "ACTIVE" | "ARCHIVED"; skus: DsSku[]; publish_problems: string[];
}
export interface StoreSettings {
  open: boolean; shipping_flat_fee: string | null; free_shipping_over: string | null; sales_tax_acknowledged: boolean;
  /** shipping priced by order weight; when present it replaces the flat fee */
  shipping_tiers?: { max_lb: string | null; fee: string }[] | null;
  /** "STRIPE_TAX": Stripe works out the tax at checkout; "NONE" (the default): no tax is collected and the owner has acknowledged that */
  sales_tax_mode?: "NONE" | "STRIPE_TAX"; tax_code?: string;
  acknowledged_by?: string | null; acknowledged_at?: string | null;
}
export interface StoreView { settings: StoreSettings; open_to_shoppers: boolean; blockers: string[] }
export interface ImportRow { row: number; sku_code: string | null; action: string; problems: string[] }
export interface ImportReport {
  ok: boolean; committed: boolean; token: string | null; file_problems: string[];
  summary: { rows: number; skus_created: number; skus_updated: number; skus_unchanged: number; products_created: number; products_updated: number; rejected: number };
  rows: ImportRow[];
}
export interface NewProduct {
  name: string; category?: string; description?: string; is_non_perishable: boolean;
  skus: { sku_code: string; price: string; supplier: string; supplier_sku?: string; supplier_cost?: string; availability: Availability }[];
}

const post = (token: string, path: string, body?: unknown) =>
  request<{ order: DsOrder }>(`${BASE}${path}`, { method: "POST", headers: authHeaders(token), body: body === undefined ? undefined : JSON.stringify(body) });

/** A readable message plus the list of reasons a refusal came with (the server sends `problems` for a refused request). */
export function describeError(err: unknown, fallback: string): { message: string; problems: string[] } {
  if (err instanceof ApiError) {
    const problems = err.details && Array.isArray(err.details.problems) ? (err.details.problems as unknown[]).map(String) : [];
    return { message: err.message || fallback, problems };
  }
  return { message: fallback, problems: [] };
}

// --- roles: the server is the authority; these only decide which buttons to show ---
export const canManage = (role?: string | null) => role === "OWNER" || role === "ADMIN" || role === "MANAGER";
export const canCoordinate = (role?: string | null) => canManage(role) || role === "STAFF";

// --- orders ---
export const listOrders = (token: string, p: { status?: string; q?: string; limit?: number } = {}) => {
  const qs = new URLSearchParams();
  if (p.status) qs.set("status", p.status);
  if (p.q) qs.set("q", p.q);
  qs.set("limit", String(p.limit ?? 100));
  return request<{ orders: DsOrder[] }>(`${BASE}/orders?${qs}`, { headers: authHeaders(token) });
};
export const getOrder = (token: string, id: string) => request<{ order: DsOrder }>(`${BASE}/orders/${id}`, { headers: authHeaders(token) });
export const markOrderPaid = (token: string, id: string, payment_reference: string) => post(token, `/orders/${id}/mark-paid`, { payment_reference });
export const cancelOrder = (token: string, id: string, reason: string) => post(token, `/orders/${id}/cancel`, { reason });
export const requestRefund = (token: string, id: string, reason: string) => post(token, `/orders/${id}/request-refund`, { reason });
export const erasePersonalData = (token: string, id: string) => post(token, `/orders/${id}/erase-personal-data`);

// --- the supplier work queue ---
export const listSupplierOrders = (token: string, status?: SupplierOrderStatus) =>
  request<{ supplier_orders: WorkQueueEntry[] }>(`${BASE}/supplier-orders${status ? `?status=${status}` : ""}`, { headers: authHeaders(token) });
/** The queue as a spreadsheet for the supplier: fetched with the staff token, then saved by the browser (a plain link could not carry the token). */
export async function downloadSupplierOrdersCsv(token: string, status: SupplierOrderStatus): Promise<void> {
  const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}${BASE}/supplier-orders/export?status=${status}`, { headers: authHeaders(token) });
  if (!res.ok) throw new ApiError(res.status, res.status === 403 ? "Only staff who work orders can download this file." : "Could not make the file. Please try again.");
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = `supplier-orders-${status.toLowerCase()}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}
export const placeSupplierOrder = (token: string, id: string, supplier_reference: string, note?: string) =>
  post(token, `/supplier-orders/${id}/placed`, { supplier_reference, note: note || undefined });
export const confirmSupplierOrder = (token: string, id: string) => post(token, `/supplier-orders/${id}/confirmed`, {});
export const failSupplierOrder = (token: string, id: string, note: string) => post(token, `/supplier-orders/${id}/failed`, { note });
export const addShipment = (token: string, id: string, b: { carrier?: string; tracking_number: string; tracking_url?: string }) =>
  post(token, `/supplier-orders/${id}/shipments`, b);
export const markShipment = (token: string, id: string, status: "delivered" | "exception", note?: string) => post(token, `/shipments/${id}/${status}`, { note: note || undefined });

// --- catalog ---
export const listProducts = (token: string, p: { q?: string; status?: string } = {}) => {
  const qs = new URLSearchParams({ limit: "200" });
  if (p.q) qs.set("q", p.q);
  if (p.status) qs.set("status", p.status);
  return request<{ products: DsProduct[] }>(`${BASE}/products?${qs}`, { headers: authHeaders(token) });
};
export const createProduct = (token: string, body: NewProduct) =>
  request<{ product: DsProduct }>(`${BASE}/products`, { method: "POST", headers: authHeaders(token), body: JSON.stringify(body) });
export const publishProduct = (token: string, id: string) => request<{ product: DsProduct }>(`${BASE}/products/${id}/publish`, { method: "POST", headers: authHeaders(token) });
export const archiveProduct = (token: string, id: string) => request<{ product: DsProduct }>(`${BASE}/products/${id}/archive`, { method: "POST", headers: authHeaders(token) });
export const setAvailability = (token: string, skuId: string, availability: Availability) =>
  request<{ sku: DsSku }>(`${BASE}/skus/${skuId}/availability`, { method: "PATCH", headers: authHeaders(token), body: JSON.stringify({ availability }) });
export const previewImport = (token: string, csv_text: string) =>
  request<ImportReport>(`${BASE}/catalog/import/preview`, { method: "POST", headers: authHeaders(token), body: JSON.stringify({ csv_text }) });
export const commitImport = (token: string, csv_text: string, importToken: string) =>
  request<ImportReport>(`${BASE}/catalog/import/commit`, { method: "POST", headers: authHeaders(token), body: JSON.stringify({ csv_text, token: importToken }) });

export interface DsSupplier { id: string; name: string; integration_type: string; status: string }
export const listSuppliers = (token: string) => request<{ suppliers: DsSupplier[] }>(`${BASE}/suppliers`, { headers: authHeaders(token) });
export const createSupplier = (token: string, name: string) =>
  request<{ supplier: DsSupplier }>(`${BASE}/suppliers`, { method: "POST", headers: authHeaders(token), body: JSON.stringify({ name }) });

// --- the store's own settings ---
export const getStoreSettings = (token: string) => request<StoreView>(`${BASE}/store-settings`, { headers: authHeaders(token) });
export const putStoreSettings = (token: string, body: Partial<StoreSettings>) =>
  request<StoreView>(`${BASE}/store-settings`, { method: "PUT", headers: authHeaders(token), body: JSON.stringify(body) });

// --- the daily stock and price sync of a supplier's feed ---
export interface SyncSettings {
  feed_url: string | null; markup_percent: string | null; stock_buffer: number; max_price_change_percent: string; sync_enabled: boolean;
  last_sync_at: string | null; last_sync_summary: SyncSummary | null;
}
export interface SyncSummary {
  applied?: boolean; error?: string; source?: string; dry_run?: boolean; matched?: number; unchanged?: number; not_in_feed?: number; unknown_in_feed?: number;
  unknown_in_feed_sample?: string[]; to_available?: number; to_out_of_stock?: number; prices_changed?: number; costs_changed?: number;
  held_prices?: { sku_code: string; from: string; to: string; change_percent: string }[]; held_price_count?: number; availability_held?: boolean; problems?: string[];
}
export type SyncableSupplier = DsSupplier & { sync?: SyncSettings };
export const saveSyncSettings = (token: string, supplierId: string, body: { feed_url: string | null; markup_percent: string | null; stock_buffer: number; max_price_change_percent: string; sync_enabled: boolean }) =>
  request<SyncableSupplier>(`${BASE}/suppliers/${supplierId}/sync-settings`, { method: "PUT", headers: authHeaders(token), body: JSON.stringify(body) });
export const runSupplierSync = (token: string, supplierId: string, body: { csv_text?: string; dry_run: boolean; force?: boolean }) =>
  request<SyncSummary>(`${BASE}/suppliers/${supplierId}/sync`, { method: "POST", headers: authHeaders(token), body: JSON.stringify(body) });
