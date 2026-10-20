// The public storefront API (/api/v1/public/store/*): no login, no tenant in the URL (the server serves exactly one business).
// Works on the server (for the pages search engines read) and in the browser. The browser never decides a price: totals come back from the server.
import { API_BASE } from "@/lib/storeConfig";

const BASE = `${API_BASE}/api/v1/public/store`;

export interface StoreInfo {
  open: true; currency: string; shipping: string; free_shipping_over: string | null; flat_shipping_fee: string | null; sales_tax_notice: string; ships_to: string;
}
export interface StoreItem { sku: string; price: string; currency: string; in_stock: boolean }
export interface StoreProduct {
  name: string; slug: string; description: string | null; category: string | null; image_url: string | null; non_perishable: boolean; items: StoreItem[];
}
export interface StoreOrder {
  order_number: string; status: string; status_label: string; currency: string; placed_at: string | null; subtotal: string; shipping: string; tax: string; total: string;
  can_pay: boolean; items: { name: string; quantity: number; unit_price: string; line_total: string }[];
  tracking: { carrier: string | null; tracking_number: string; tracking_url: string | null; status: string }[];
}
export interface CheckoutBody {
  lines: { sku: string; quantity: number }[];
  contact: { name: string; email: string; phone?: string };
  ship_to: { name: string; line1: string; line2?: string; city: string; state: string; postal_code: string };
  idempotency_key: string;
}
export interface CheckoutResult { order_number: string; status_token: string; status: string; total: string; checkout_url: string | null }

/** A refusal from the store: a readable message and the server's list of reasons (for example a bad ZIP or a sold-out item). */
export class StoreApiError extends Error {
  status: number;
  problems: string[];
  constructor(status: number, message: string, problems: string[] = []) {
    super(message);
    this.status = status;
    this.problems = problems;
  }
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, { ...init, headers: { "Content-Type": "application/json", ...(init.headers ?? {}) } });
  } catch {
    throw new StoreApiError(0, "The store could not be reached. Please try again in a moment.");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = (body as { detail?: unknown }).detail;
    let message = "Something went wrong. Please try again.";
    let problems: string[] = [];
    if (typeof detail === "string") message = detail;
    else if (detail && typeof detail === "object") {
      const d = detail as { message?: unknown; problems?: unknown };
      if (typeof d.message === "string") message = d.message;
      if (Array.isArray(d.problems)) problems = d.problems.map(String);
    }
    if (res.status === 429) message = "Too many requests. Please wait a minute and try again.";
    throw new StoreApiError(res.status, message, problems);
  }
  return (await res.json()) as T;
}

/** Catalog reads are cached for a minute on the server: prices and stock are at most a minute old on a page, and the cart re-reads them live. */
const REVALIDATE: RequestInit = { next: { revalidate: 60 } };
const LIVE: RequestInit = { cache: "no-store" };

/** null when the store is closed or does not exist (the API answers 404 for both and says nothing more). */
export async function getStoreInfo(): Promise<StoreInfo | null> {
  try {
    return await call<StoreInfo>("", REVALIDATE);
  } catch (err) {
    if (err instanceof StoreApiError && err.status === 404) return null;
    throw err;
  }
}

export async function listStoreProducts(p: { q?: string; category?: string; limit?: number; offset?: number } = {}): Promise<StoreProduct[]> {
  const qs = new URLSearchParams();
  if (p.q) qs.set("q", p.q);
  if (p.category) qs.set("category", p.category);
  qs.set("limit", String(p.limit ?? 24));
  qs.set("offset", String(p.offset ?? 0));
  return (await call<{ products: StoreProduct[] }>(`/products?${qs}`, REVALIDATE)).products;
}

/** null when there is no such product (or it is not on sale). */
export async function getStoreProduct(slug: string, opts: { live?: boolean } = {}): Promise<StoreProduct | null> {
  try {
    return (await call<{ product: StoreProduct }>(`/products/${encodeURIComponent(slug)}`, opts.live ? LIVE : REVALIDATE)).product;
  } catch (err) {
    if (err instanceof StoreApiError && err.status === 404) return null;
    throw err;
  }
}

export const createCheckout = (body: CheckoutBody) => call<CheckoutResult>("/checkout", { ...LIVE, method: "POST", body: JSON.stringify(body) });
export const getStoreOrder = async (token: string) => (await call<{ order: StoreOrder }>(`/orders/${encodeURIComponent(token)}`, LIVE)).order;
export const payStoreOrder = (token: string) => call<CheckoutResult>(`/orders/${encodeURIComponent(token)}/pay`, { ...LIVE, method: "POST" });
