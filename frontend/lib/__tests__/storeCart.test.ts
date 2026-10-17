import { describe, expect, it } from "vitest";
import { MAX_LINES, MAX_QTY, addLine, cartCount, cartFingerprint, estimate, formatMoney, fromCents, parseCart, priceLines, removeLine, setQuantity, toCents } from "@/lib/storeCart";
import type { StoreProduct } from "@/lib/storeApi";

const tea = { sku: "TEA-1", slug: "green-tea", name: "Green Tea" };
const rice = { sku: "RICE-1", slug: "brown-rice", name: "Brown Rice" };
const catalog = (price = "10.00", in_stock = true): Map<string, StoreProduct | null> => new Map([
  ["green-tea", { name: "Green Tea", slug: "green-tea", description: null, category: null, image_url: null, non_perishable: true, items: [{ sku: "TEA-1", price, currency: "USD", in_stock }] }],
  ["brown-rice", null],
]);
const rule = { flat_shipping_fee: "5.00", free_shipping_over: "50.00" };

describe("basket", () => {
  it("merges the same item and caps the quantity", () => {
    let lines = addLine([], tea, 2);
    lines = addLine(lines, tea, 3);
    expect(lines).toEqual([{ ...tea, quantity: 5 }]);
    expect(addLine(lines, tea, 500)[0].quantity).toBe(MAX_QTY);
  });

  it("changes a quantity, and removes a line when it goes below 1", () => {
    const lines = addLine(addLine([], tea), rice);
    expect(setQuantity(lines, "TEA-1", 7).find((l) => l.sku === "TEA-1")?.quantity).toBe(7);
    expect(setQuantity(lines, "TEA-1", 0).map((l) => l.sku)).toEqual(["RICE-1"]);
    expect(setQuantity(lines, "TEA-1", NaN).map((l) => l.sku)).toEqual(["RICE-1"]);
    expect(removeLine(lines, "RICE-1").map((l) => l.sku)).toEqual(["TEA-1"]);
    expect(cartCount(lines)).toBe(2);
  });

  it("holds a limited number of lines", () => {
    let lines: ReturnType<typeof addLine> = [];
    for (let i = 0; i < MAX_LINES + 5; i++) lines = addLine(lines, { sku: `S${i}`, slug: `s${i}`, name: "x" });
    expect(lines).toHaveLength(MAX_LINES);
  });

  it("reads a saved basket defensively", () => {
    expect(parseCart(null)).toEqual([]);
    expect(parseCart("not json")).toEqual([]);
    expect(parseCart('{"a":1}')).toEqual([]);
    const saved = JSON.stringify([{ ...tea, quantity: 3 }, { ...tea, quantity: 9 }, { sku: "", slug: "x", name: "x", quantity: 1 }, { sku: "A", slug: "a", name: "a", quantity: "lots" }, { ...rice, quantity: 1000 }, null]);
    expect(parseCart(saved)).toEqual([{ ...tea, quantity: 3 }, { ...rice, quantity: MAX_QTY }]);
  });
});

describe("money", () => {
  it("is exact in whole cents", () => {
    expect(toCents("10.00")).toBe(1000);
    expect(toCents("0.1")).toBe(10);
    expect(toCents("19.99")).toBe(1999);
    expect(Number.isNaN(toCents("$5"))).toBe(true);
    expect(Number.isNaN(toCents(null))).toBe(true);
    expect(fromCents(1999)).toBe("19.99");
    expect(fromCents(5)).toBe("0.05");
    expect(formatMoney("5.00")).toBe("$5.00");
    expect(formatMoney("5.00", "EUR")).toBe("5.00 EUR");
  });

  it("sums without floating point drift", () => {
    const lines = priceLines([{ ...tea, quantity: 3 }], catalog("0.10"));
    expect(estimate(null, lines).subtotalCents).toBe(30);
  });
});

describe("pricing the basket from what the store says now", () => {
  it("uses today's price, not anything saved", () => {
    const [l] = priceLines([{ ...tea, quantity: 2 }], catalog("12.50"));
    expect(l.price).toBe("12.50");
    expect(l.lineCents).toBe(2500);
  });

  it("marks sold-out and vanished items, and blocks checkout for them", () => {
    const lines = priceLines([{ ...tea, quantity: 1 }, { ...rice, quantity: 1 }], catalog("10.00", false));
    expect(lines[0]).toMatchObject({ inStock: false, gone: false });
    expect(lines[1]).toMatchObject({ gone: true, price: null, lineCents: null });
    expect(estimate(rule, lines).blocked).toBe(true);
    expect(estimate(rule, priceLines([{ ...tea, quantity: 1 }], catalog())).blocked).toBe(false);
    expect(estimate(rule, []).blocked).toBe(true);
  });

  it("estimates shipping from the published rule: a flat fee, free from the threshold", () => {
    const under = estimate(rule, priceLines([{ ...tea, quantity: 4 }], catalog()));        // 40.00
    expect([under.subtotalCents, under.shippingCents, under.totalCents]).toEqual([4000, 500, 4500]);
    const at = estimate(rule, priceLines([{ ...tea, quantity: 5 }], catalog()));            // 50.00 reaches it
    expect([at.shippingCents, at.totalCents]).toEqual([0, 5000]);
    expect(estimate({ flat_shipping_fee: "0.00", free_shipping_over: null }, priceLines([{ ...tea, quantity: 1 }], catalog())).shippingCents).toBe(0);
    expect(estimate({ flat_shipping_fee: null, free_shipping_over: null }, priceLines([{ ...tea, quantity: 1 }], catalog())).shippingCents).toBe(0);
  });
});

describe("basket fingerprint", () => {
  it("does not depend on order and changes when the basket changes", () => {
    const a = [{ ...tea, quantity: 1 }, { ...rice, quantity: 2 }];
    expect(cartFingerprint(a)).toBe(cartFingerprint([...a].reverse()));
    expect(cartFingerprint(a)).not.toBe(cartFingerprint([{ ...tea, quantity: 2 }, { ...rice, quantity: 2 }]));
  });
});
