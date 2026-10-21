import { describe, expect, it } from "vitest";
import type { StoreProduct } from "@/lib/storeApi";
import { PAGE_SIZE, aisleCounts, filterHref, filterProducts, isDefault, paginate, parseFilters, sortProducts } from "@/lib/storeCatalog";

const p = (name: string, price: string, over: Partial<StoreProduct> = {}): StoreProduct => ({
  name, slug: name.toLowerCase().replace(/\W+/g, "-"), description: null, category: "Pantry", image_url: null, non_perishable: true,
  items: [{ sku: name, price, currency: "USD", in_stock: true }], ...over,
});
const F = parseFilters({});

describe("parseFilters", () => {
  it("falls back to the defaults for anything missing or unknown", () => {
    expect(F).toEqual({ q: "", category: "", sort: "featured", stock: false, price: "", page: 1 });
    expect(parseFilters({ sort: "evil", price: "x", stock: "yes", page: "-3" })).toEqual(F);
    expect(parseFilters({ page: ["4", "5"], q: ["  oats  "] })).toMatchObject({ page: 4, q: "oats" });
    expect(parseFilters({ q: "x".repeat(500) }).q).toHaveLength(80);
    expect(isDefault(F)).toBe(true);
    expect(isDefault({ ...F, stock: true })).toBe(false);
  });
});

describe("filterHref", () => {
  it("leaves defaults out and goes back to page 1 when a filter changes", () => {
    expect(filterHref(F)).toBe("/store#products");
    expect(filterHref({ ...F, page: 3, category: "Tea" }, { sort: "name" })).toBe("/store?category=Tea&sort=name#products");
    expect(filterHref({ ...F, category: "Tea" }, { page: 2 })).toBe("/store?category=Tea&page=2#products");
    expect(filterHref({ ...F, q: "tea & co", stock: true, price: "u5" })).toBe("/store?q=tea+%26+co&stock=1&price=u5#products");
    expect(filterHref(F, {}, "")).toBe("/store");
  });
});

describe("filterProducts", () => {
  const all = [p("Green Tea", "10.00", { category: "Tea", description: "Loose leaf" }), p("Black Tea", "4.99", { category: "Tea" }), p("Oats", "5.00"), p("Rice", "20.00", { items: [{ sku: "R", price: "20.00", currency: "USD", in_stock: false }] })];

  it("searches name, aisle and description, every word having to match", () => {
    expect(filterProducts(all, { ...F, q: "tea" }).map((x) => x.name)).toEqual(["Green Tea", "Black Tea"]);
    expect(filterProducts(all, { ...F, q: "loose tea" }).map((x) => x.name)).toEqual(["Green Tea"]);
    expect(filterProducts(all, { ...F, q: "pantry" }).map((x) => x.name)).toEqual(["Oats", "Rice"]);
    expect(filterProducts(all, { ...F, q: "nothing" })).toEqual([]);
  });

  it("treats a price band as from its lower edge up to, not including, its upper edge", () => {
    expect(filterProducts(all, { ...F, price: "u5" }).map((x) => x.name)).toEqual(["Black Tea"]);
    expect(filterProducts(all, { ...F, price: "5-10" }).map((x) => x.name)).toEqual(["Oats"]);      // 5.00 belongs to 5 to 10, not under 5
    expect(filterProducts(all, { ...F, price: "10-20" }).map((x) => x.name)).toEqual(["Green Tea"]);
    expect(filterProducts(all, { ...F, price: "o20" }).map((x) => x.name)).toEqual(["Rice"]);
  });

  it("can show only what is in stock, and narrows by aisle", () => {
    expect(filterProducts(all, { ...F, stock: true }).map((x) => x.name)).not.toContain("Rice");
    expect(filterProducts(all, { ...F, category: "Tea" })).toHaveLength(2);
  });
});

describe("sortProducts", () => {
  const all = [p("Mid", "5.00"), p("Sold", "1.00", { items: [{ sku: "S", price: "1.00", currency: "USD", in_stock: false }] }), p("Cheap", "2.00"), p("Dear", "9.00")];
  it("orders by price or name, and puts what can be bought first when featured", () => {
    expect(sortProducts(all, "price-asc").map((x) => x.name)).toEqual(["Sold", "Cheap", "Mid", "Dear"]);
    expect(sortProducts(all, "price-desc").map((x) => x.name)).toEqual(["Dear", "Mid", "Cheap", "Sold"]);
    expect(sortProducts(all, "name").map((x) => x.name)).toEqual(["Cheap", "Dear", "Mid", "Sold"]);
    expect(sortProducts(all, "featured").map((x) => x.name)).toEqual(["Mid", "Cheap", "Dear", "Sold"]);    // the store's own order, sold-out last
  });
  it("never changes the list it was given", () => {
    const copy = [...all];
    sortProducts(all, "name");
    expect(all).toEqual(copy);
  });
});

describe("aisleCounts and paginate", () => {
  it("counts each aisle under the other filters but not its own", () => {
    const all = [p("A", "1.00", { category: "Tea" }), p("B", "9.00", { category: "Tea" }), p("C", "1.50", { category: "Rice" })];
    expect([...aisleCounts(all, { ...F, price: "u5", category: "Tea" })]).toEqual([["Tea", 1], ["Rice", 1]]);   // choosing Tea does not hide Rice's count
  });
  it("pages by 24 and never past the last page", () => {
    const list = Array.from({ length: PAGE_SIZE * 2 + 1 }, (_, i) => i);
    expect(paginate(list, 1)).toMatchObject({ hasNext: true, pages: 3 });
    expect(paginate(list, 3).items).toEqual([48]);
    expect(paginate(list, 99).items).toEqual([48]);
    expect(paginate([], 1)).toEqual({ items: [], hasNext: false, pages: 1 });
  });
});
