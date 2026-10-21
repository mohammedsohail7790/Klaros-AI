// How the shop's product list is narrowed and ordered. Pure functions (no React, no network), so they are easy to test. The shop page reads the whole catalog once and applies these
// in memory: a small store's catalog fits comfortably, and it lets every filter and every sort order be a plain link that works without scripts.
import type { StoreProduct } from "@/lib/storeApi";

export type Sort = "featured" | "price-asc" | "price-desc" | "name";
export type PriceBand = "" | "u5" | "5-10" | "10-20" | "o20";

export interface Filters { q: string; category: string; sort: Sort; stock: boolean; price: PriceBand; page: number }

export const PAGE_SIZE = 24;
export const SORTS: { id: Sort; label: string }[] = [
  { id: "featured", label: "Featured" },
  { id: "price-asc", label: "Price: low to high" },
  { id: "price-desc", label: "Price: high to low" },
  { id: "name", label: "Name: A to Z" },
];
export const PRICE_BANDS: { id: Exclude<PriceBand, "">; label: string; min: number; max: number }[] = [
  { id: "u5", label: "Under $5", min: 0, max: 5 },
  { id: "5-10", label: "$5 to $10", min: 5, max: 10 },
  { id: "10-20", label: "$10 to $20", min: 10, max: 20 },
  { id: "o20", label: "Over $20", min: 20, max: Infinity },
];

type Raw = Record<string, string | string[] | undefined>;
const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v) ?? "";

/** Reads the page's query string defensively: anything unknown falls back to the default. */
export function parseFilters(sp: Raw): Filters {
  const sort = one(sp.sort) as Sort;
  const price = one(sp.price) as PriceBand;
  return {
    q: one(sp.q).trim().slice(0, 80),
    category: one(sp.category).trim().slice(0, 120),
    sort: SORTS.some((s) => s.id === sort) ? sort : "featured",
    stock: one(sp.stock) === "1",
    price: PRICE_BANDS.some((b) => b.id === price) ? price : "",
    page: Math.max(1, Math.floor(Number(one(sp.page))) || 1),
  };
}

export const isDefault = (f: Filters) => !f.q && !f.category && f.sort === "featured" && !f.stock && !f.price && f.page === 1;

/** /store?... for these filters with some of them changed. Defaults are left out, and changing any filter returns to page 1 unless `page` is given. */
export function filterHref(f: Filters, over: Partial<Filters> = {}, hash = "products"): string {
  const next: Filters = { ...f, page: 1, ...over };
  const qs = new URLSearchParams();
  if (next.q) qs.set("q", next.q);
  if (next.category) qs.set("category", next.category);
  if (next.sort !== "featured") qs.set("sort", next.sort);
  if (next.stock) qs.set("stock", "1");
  if (next.price) qs.set("price", next.price);
  if (next.page > 1) qs.set("page", String(next.page));
  const s = qs.toString();
  return `/store${s ? `?${s}` : ""}${hash ? `#${hash}` : ""}`;
}

/** The lowest price among a product's items (NaN when it has none). */
export const lowestPrice = (p: StoreProduct) => Math.min(...p.items.map((i) => Number(i.price)).filter((n) => !Number.isNaN(n)), Infinity);
export const inStock = (p: StoreProduct) => p.items.some((i) => i.in_stock);

function matches(p: StoreProduct, f: Filters, ignore?: "category"): boolean {
  if (f.q) {
    const hay = `${p.name} ${p.category ?? ""} ${p.description ?? ""}`.toLowerCase();
    if (!f.q.toLowerCase().split(/\s+/).every((w) => hay.includes(w))) return false;
  }
  if (ignore !== "category" && f.category && p.category !== f.category) return false;
  if (f.stock && !inStock(p)) return false;
  if (f.price) {
    const band = PRICE_BANDS.find((b) => b.id === f.price)!;
    const lo = lowestPrice(p);
    if (!(lo >= band.min && lo < band.max)) return false;
  }
  return true;
}

export function filterProducts(all: StoreProduct[], f: Filters): StoreProduct[] {
  return all.filter((p) => matches(p, f));
}

export function sortProducts(list: StoreProduct[], sort: Sort): StoreProduct[] {
  const out = [...list];
  if (sort === "price-asc") out.sort((a, b) => lowestPrice(a) - lowestPrice(b));
  else if (sort === "price-desc") out.sort((a, b) => lowestPrice(b) - lowestPrice(a));
  else if (sort === "name") out.sort((a, b) => a.name.localeCompare(b.name));
  else out.sort((a, b) => Number(inStock(b)) - Number(inStock(a))); // featured: what can be bought comes first, otherwise the store's own order (the sort is stable)
  return out;
}

/** How many products each aisle would show under the other filters (so a count never promises more than the list delivers). */
export function aisleCounts(all: StoreProduct[], f: Filters): Map<string, number> {
  const counts = new Map<string, number>();
  for (const p of all) if (p.category && matches(p, f, "category")) counts.set(p.category, (counts.get(p.category) ?? 0) + 1);
  return counts;
}

export function paginate<T>(list: T[], page: number): { items: T[]; hasNext: boolean; pages: number } {
  const pages = Math.max(1, Math.ceil(list.length / PAGE_SIZE));
  const p = Math.min(page, pages);
  return { items: list.slice((p - 1) * PAGE_SIZE, p * PAGE_SIZE), hasNext: p < pages, pages };
}
