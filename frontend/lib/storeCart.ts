// Pure cart logic for the storefront (no React, no network), so it is easy to test. A cart line holds only an item code and a quantity, plus the product slug and name
// to show before prices are re-read. Prices are never stored: they are re-read from the store every time the cart opens, and the server prices the real order.
import type { ShippingTier, StoreInfo, StoreProduct } from "@/lib/storeApi";

export interface CartLine { sku: string; slug: string; name: string; quantity: number }
export const MAX_QTY = 99;
export const MAX_LINES = 50;

const clamp = (n: number) => Math.max(1, Math.min(MAX_QTY, Math.floor(n)));

export function addLine(lines: CartLine[], item: Omit<CartLine, "quantity">, quantity = 1): CartLine[] {
  const existing = lines.find((l) => l.sku === item.sku);
  if (existing) return lines.map((l) => (l.sku === item.sku ? { ...l, quantity: clamp(l.quantity + quantity) } : l));
  if (lines.length >= MAX_LINES) return lines;
  return [...lines, { ...item, quantity: clamp(quantity) }];
}

/** A quantity of 0 or less removes the line. */
export function setQuantity(lines: CartLine[], sku: string, quantity: number): CartLine[] {
  if (!Number.isFinite(quantity) || quantity < 1) return lines.filter((l) => l.sku !== sku);
  return lines.map((l) => (l.sku === sku ? { ...l, quantity: clamp(quantity) } : l));
}

export const removeLine = (lines: CartLine[], sku: string) => lines.filter((l) => l.sku !== sku);
export const cartCount = (lines: CartLine[]) => lines.reduce((n, l) => n + l.quantity, 0);

/** Reads a saved cart defensively: anything malformed is dropped, never trusted. */
export function parseCart(raw: string | null): CartLine[] {
  if (!raw) return [];
  try {
    const data = JSON.parse(raw);
    if (!Array.isArray(data)) return [];
    const out: CartLine[] = [];
    for (const l of data) {
      if (!l || typeof l.sku !== "string" || !l.sku || typeof l.slug !== "string" || typeof l.name !== "string" || typeof l.quantity !== "number" || !Number.isFinite(l.quantity)) continue;
      if (out.some((o) => o.sku === l.sku) || out.length >= MAX_LINES) continue;
      out.push({ sku: l.sku, slug: l.slug, name: l.name.slice(0, 200), quantity: clamp(l.quantity) });
    }
    return out;
  } catch {
    return [];
  }
}

/** Money is held as whole cents so sums are exact. NaN for anything that is not a plain amount. */
export function toCents(amount: string | null | undefined): number {
  if (amount == null || !/^\d+(\.\d{1,2})?$/.test(amount.trim())) return NaN;
  const [whole, frac = ""] = amount.trim().split(".");
  return Number(whole) * 100 + Number(frac.padEnd(2, "0"));
}
export const fromCents = (cents: number) => `${Math.floor(cents / 100)}.${String(cents % 100).padStart(2, "0")}`;

/** "$10.00" for US dollars (the only currency the store sells in), otherwise the amount and its currency code. */
export const formatMoney = (amount: string, currency = "USD") => (currency === "USD" ? `$${amount}` : `${amount} ${currency}`);

export interface PricedLine extends CartLine {
  /** the store no longer sells this item */
  gone: boolean;
  inStock: boolean;
  price: string | null;
  lineCents: number | null;
  imageUrl: string | null;
  category: string | null;
  /** the item's weight in grams, when the store has one (shipping by weight needs it) */
  weightG: number | null;
}

/** Joins the cart with what the store says now. `products` maps slug to the product, or null when the store no longer has it. */
export function priceLines(lines: CartLine[], products: Map<string, StoreProduct | null>): PricedLine[] {
  return lines.map((l) => {
    const item = products.get(l.slug)?.items.find((i) => i.sku === l.sku);
    if (!item) return { ...l, gone: true, inStock: false, price: null, lineCents: null, imageUrl: null, category: null, weightG: null };
    const cents = toCents(item.price);
    const product = products.get(l.slug)!;
    return { ...l, name: product.name, gone: false, inStock: item.in_stock, price: item.price, lineCents: Number.isNaN(cents) ? null : cents * l.quantity, imageUrl: product.image_url, category: product.category, weightG: item.weight_g ?? null };
  });
}

export interface Estimate {
  subtotalCents: number; shippingCents: number; totalCents: number;
  /** something in the basket cannot be bought right now (or is too heavy to ship) */
  blocked: boolean;
  /** false when shipping goes by weight and an item has no weight: it is then worked out at checkout */
  shippingKnown: boolean;
  /** the basket weighs more than the store ships in one order */
  tooHeavy: boolean;
}

export const GRAMS_PER_POUND = 453.59237;

/** The fee in cents of the first tier whose limit covers this many grams, or null when the order is heavier than every limited tier and there is no open-ended one (the server's own rule). */
export function tierFeeCents(tiers: ShippingTier[], grams: number): number | null {
  const pounds = grams / GRAMS_PER_POUND;
  for (const t of tiers) if (t.max_lb === null || pounds <= Number(t.max_lb)) return toCents(t.fee);
  return null;
}

/** An ESTIMATE from the published shipping rule. The server prices the real order and Stripe charges the server's total. */
export function estimate(info: Pick<StoreInfo, "flat_shipping_fee" | "free_shipping_over" | "shipping_tiers"> | null, lines: PricedLine[]): Estimate {
  let blocked = lines.length === 0 || lines.some((l) => l.gone || !l.inStock || l.lineCents == null);
  const subtotalCents = lines.reduce((n, l) => n + (l.lineCents ?? 0), 0);
  const freeOver = toCents(info?.free_shipping_over);
  const free = !Number.isNaN(freeOver) && subtotalCents >= freeOver;
  let shippingCents = 0, shippingKnown = true, tooHeavy = false;
  const tiers = info?.shipping_tiers;
  if (lines.length > 0 && !free) {
    if (tiers && tiers.length > 0) {
      if (lines.some((l) => l.weightG == null || l.weightG <= 0)) shippingKnown = false;
      else {
        const fee = tierFeeCents(tiers, lines.reduce((g, l) => g + (l.weightG as number) * l.quantity, 0));
        if (fee === null) { tooHeavy = true; blocked = true; } else shippingCents = fee;
      }
    } else {
      const fee = toCents(info?.flat_shipping_fee);
      shippingCents = Number.isNaN(fee) ? 0 : fee;
    }
  }
  return { subtotalCents, shippingCents, totalCents: subtotalCents + shippingCents, blocked, shippingKnown, tooHeavy };
}

/** A stable fingerprint of the basket: the checkout idempotency key is kept while this is unchanged and renewed when it changes. */
export const cartFingerprint = (lines: CartLine[]) => [...lines].sort((a, b) => a.sku.localeCompare(b.sku)).map((l) => `${l.sku}:${l.quantity}`).join("|");
