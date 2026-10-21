import { describe, expect, it } from "vitest";
import type { ShippingTier, StoreProduct } from "@/lib/storeApi";
import { estimate, priceLines, tierFeeCents } from "@/lib/storeCart";

// US Direct's rates plus $1 each: the store's own tiers
const TIERS: ShippingTier[] = [["1", "6.95"], ["5", "10.95"], ["10", "15.95"], ["15", "20.95"], ["20", "25.95"], ["25", "27.95"], ["30", "29.95"], ["40", "33.95"], ["50", "43.95"], [null, "53.95"]].map(([max_lb, fee]) => ({ max_lb, fee }) as ShippingTier);
const LB = 453.59237;

describe("tierFeeCents", () => {
  it("takes the first tier that covers the weight, the limit itself included", () => {
    expect(tierFeeCents(TIERS, 200)).toBe(695);
    expect(tierFeeCents(TIERS, LB)).toBe(695);                    // exactly 1 lb
    expect(tierFeeCents(TIERS, LB + 1)).toBe(1095);
    expect(tierFeeCents(TIERS, 5 * LB)).toBe(1095);
    expect(tierFeeCents(TIERS, 50 * LB)).toBe(4395);
    expect(tierFeeCents(TIERS, 50 * LB + 10)).toBe(5395);         // the open-ended tier
    expect(tierFeeCents(TIERS, 400 * LB)).toBe(5395);
  });
  it("gives nothing when no tier covers the weight", () => {
    expect(tierFeeCents([{ max_lb: "10", fee: "5.00" }], 11 * LB)).toBeNull();
  });
});

const product = (sku: string, price: string, weight_g: number | null): StoreProduct => ({ name: sku, slug: sku.toLowerCase(), description: null, category: null, image_url: null, non_perishable: true, items: [{ sku, price, currency: "USD", in_stock: true, weight_g }] });
const basket = (weights: (number | null)[], qty = 1) => {
  const products = weights.map((w, i) => product(`S${i}`, "10.00", w));
  const lines = products.map((p) => ({ sku: p.items[0].sku, slug: p.slug, name: p.name, quantity: qty }));
  return priceLines(lines, new Map(products.map((p) => [p.slug, p])));
};
const INFO = { flat_shipping_fee: "5.00", free_shipping_over: null as string | null, shipping_tiers: TIERS };

describe("estimate with shipping by weight", () => {
  it("prices the basket by its total weight", () => {
    const e = estimate(INFO, basket([300, 500]));                 // 800 g = 1.76 lb
    expect(e).toMatchObject({ subtotalCents: 2000, shippingCents: 1095, totalCents: 3095, shippingKnown: true, tooHeavy: false, blocked: false });
    expect(estimate(INFO, basket([300, 500], 3)).shippingCents).toBe(1595);   // 2400 g = 5.29 lb
  });
  it("does not guess when an item has no weight: shipping is worked out at checkout", () => {
    const e = estimate(INFO, basket([300, null]));
    expect(e).toMatchObject({ shippingKnown: false, shippingCents: 0, totalCents: 2000, blocked: false });
  });
  it("blocks a basket that is heavier than the store ships", () => {
    const e = estimate({ ...INFO, shipping_tiers: [{ max_lb: "2", fee: "5.00" }] }, basket([2000]));
    expect(e).toMatchObject({ tooHeavy: true, blocked: true });
  });
  it("is free from the threshold whatever it weighs, and a flat fee still works without tiers", () => {
    expect(estimate({ ...INFO, free_shipping_over: "20.00" }, basket([300, 500])).shippingCents).toBe(0);
    expect(estimate({ flat_shipping_fee: "5.00", free_shipping_over: null }, basket([null])).shippingCents).toBe(500);
    expect(estimate({ ...INFO, shipping_tiers: null }, basket([300])).shippingCents).toBe(500);
  });
});
