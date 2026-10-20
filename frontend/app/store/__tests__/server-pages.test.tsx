import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { INFO, product, renderInStore } from "./fixtures";

const api = vi.hoisted(() => ({ getStoreInfo: vi.fn(), listStoreProducts: vi.fn(), getStoreProduct: vi.fn() }));
vi.mock("@/lib/storeApi", async () => ({ ...(await vi.importActual<object>("@/lib/storeApi")), ...api }));
vi.mock("next/server", () => ({ connection: async () => undefined }));   // outside a real request there is nothing to wait for
import StoreHome from "@/app/store/page";
import StoreLayout, { metadata } from "@/app/store/layout";
import ProductPage, { generateMetadata } from "@/app/store/products/[slug]/page";

const sp = (o: Record<string, string> = {}) => Promise.resolve(o);
const many = (n: number) => Array.from({ length: n }, (_, i) => product({ name: `Item ${i}`, slug: `item-${i}`, category: i % 2 ? "Tea" : "Rice" }));

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  api.getStoreInfo.mockResolvedValue(INFO);
  api.listStoreProducts.mockResolvedValue([product()]);
  api.getStoreProduct.mockResolvedValue(product());
});

describe("store home (server-rendered)", () => {
  it("lists products with price and stock", async () => {
    api.listStoreProducts.mockResolvedValue([product(), product({ name: "Gone Jam", slug: "gone-jam", items: [{ sku: "J", price: "7.00", currency: "USD", in_stock: false }] })]);
    renderInStore(await StoreHome({ searchParams: sp() }));
    const grid = within(screen.getByRole("region", { name: "Products" }));
    expect(grid.getByRole("link", { name: /Green Tea/ }).closest("li")).toHaveTextContent("$10.00");
    expect(grid.getByRole("link", { name: /Gone Jam/ }).closest("li")).toHaveTextContent("Sold out");
    expect(grid.getByRole("link", { name: /Green Tea/ })).toHaveAttribute("href", "/store/products/green-tea");
    expect(grid.getByRole("button", { name: "Add Green Tea to basket" })).toBeInTheDocument();         // one tap to add
    expect(grid.queryByRole("button", { name: /Add Gone Jam/ })).not.toBeInTheDocument();             // a sold-out item cannot be added
  });

  it("passes the search, category and page to the store, and offers the next page", async () => {
    api.listStoreProducts.mockResolvedValueOnce(many(25)).mockResolvedValueOnce(many(4));
    renderInStore(await StoreHome({ searchParams: sp({ q: "tea", category: "Tea", page: "2" }) }));
    expect(api.listStoreProducts).toHaveBeenNthCalledWith(1, { q: "tea", category: "Tea", limit: 25, offset: 24 });
    expect(screen.getAllByRole("listitem")).toHaveLength(24);
    expect(screen.getByRole("link", { name: /Next page/ })).toHaveAttribute("href", "/store?q=tea&category=Tea&page=3#products");
    expect(screen.getByRole("link", { name: /Previous page/ })).toHaveAttribute("href", "/store?q=tea&category=Tea#products");
  });

  it("builds the category bar from the catalog", async () => {
    api.listStoreProducts.mockResolvedValueOnce(many(3)).mockResolvedValueOnce(many(3));
    renderInStore(await StoreHome({ searchParams: sp() }));
    const nav = screen.getByRole("navigation", { name: "Categories" });
    expect(within(nav).getAllByRole("link").map((a) => a.textContent)).toEqual(["All", "Rice", "Tea"]);
  });

  it("opens with the welcome, the trust facts and the category tiles, with real counts", async () => {
    api.listStoreProducts.mockResolvedValue(many(5));
    renderInStore(await StoreHome({ searchParams: sp() }));
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Pantry staples, delivered.");
    expect(screen.getByText("Free shipping from $50.00")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Why shop here" })).toHaveTextContent("We cannot ship to Alaska, Hawaii or US territories.");
    const tiles = within(screen.getByRole("region", { name: "Shop by category" }));
    expect(tiles.getByRole("heading", { name: "Rice" }).closest("li")).toHaveTextContent("3 products");
    expect(tiles.getByRole("heading", { name: "Tea" }).closest("li")).toHaveTextContent("2 products");
    expect(tiles.getByRole("link", { name: "Shop Rice" })).toHaveAttribute("href", "/store?category=Rice#products");
  });

  it("drops the welcome when the shopper is searching or filtering", async () => {
    renderInStore(await StoreHome({ searchParams: sp({ q: "tea" }) }));
    expect(screen.queryByRole("region", { name: "Welcome" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Results for “tea”" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Clear search" })).toHaveAttribute("href", "/store");
  });

  it("says plainly when nothing matches", async () => {
    api.listStoreProducts.mockResolvedValue([]);
    renderInStore(await StoreHome({ searchParams: sp({ q: "zzz" }) }));
    expect(screen.getByText("Nothing matches your search.")).toBeInTheDocument();
  });

  it("shows a closed-store page rather than an error when the store is closed", async () => {
    api.getStoreInfo.mockResolvedValue(null);
    renderInStore(await StoreHome({ searchParams: sp() }));
    expect(screen.getByText("The store is not open yet")).toBeInTheDocument();
    expect(api.listStoreProducts).not.toHaveBeenCalled();
  });

  it("shows 'cannot reach the store' when the API is down, and never crashes", async () => {
    api.getStoreInfo.mockRejectedValue(new Error("down"));
    renderInStore(await StoreHome({ searchParams: sp() }));
    expect(screen.getByRole("alert")).toHaveTextContent("We cannot reach the store right now");
  });

  it("ignores junk in the page and search parameters", async () => {
    renderInStore(await StoreHome({ searchParams: sp({ page: "-4", q: "x".repeat(500) }) }));
    expect(api.listStoreProducts.mock.calls[0][0].offset).toBe(0);
    expect(api.listStoreProducts.mock.calls[0][0].q).toHaveLength(80);
  });
});

describe("product page (server-rendered)", () => {
  const params = Promise.resolve({ slug: "green-tea" });

  it("shows the product and lets an in-stock item be added", async () => {
    renderInStore(await ProductPage({ params }));
    expect(screen.getByRole("heading", { name: "Green Tea" })).toBeInTheDocument();
    expect(screen.getByText("Loose leaf green tea.")).toBeInTheDocument();
    expect(screen.getByText("$10.00")).toBeInTheDocument();
    expect(screen.getByText("In stock")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add to basket" })).toBeEnabled();
    expect(within(screen.getByRole("navigation", { name: "Breadcrumb" })).getByRole("link", { name: "Tea" })).toHaveAttribute("href", "/store?category=Tea");
    expect(screen.getByText("Delivered across the continental US")).toBeInTheDocument();
    expect(screen.getByText(/Sales tax is not collected/)).toBeInTheDocument();
  });

  it("suggests other products from the same category, never the product itself", async () => {
    api.listStoreProducts.mockResolvedValue([product(), product({ name: "Jasmine Tea", slug: "jasmine-tea" }), product({ name: "Oolong Tea", slug: "oolong-tea" })]);
    renderInStore(await ProductPage({ params }));
    expect(api.listStoreProducts).toHaveBeenCalledWith({ category: "Tea", limit: 5 });
    const more = within(screen.getByRole("region", { name: "More like this" }));
    expect(more.getAllByRole("listitem").map((li) => li.textContent)).toEqual([expect.stringContaining("Jasmine Tea"), expect.stringContaining("Oolong Tea")]);
  });

  it("still shows the product when the suggestions cannot be loaded", async () => {
    api.listStoreProducts.mockRejectedValue(new Error("down"));
    renderInStore(await ProductPage({ params }));
    expect(screen.getByRole("heading", { name: "Green Tea" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "More like this" })).not.toBeInTheDocument();
  });

  it("does not let an out-of-stock item be added", async () => {
    api.getStoreProduct.mockResolvedValue(product({ items: [{ sku: "TEA-1", price: "10.00", currency: "USD", in_stock: false }] }));
    renderInStore(await ProductPage({ params }));
    expect(screen.getByText("Out of stock right now.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add to basket" })).not.toBeInTheDocument();
  });

  it("offers each option of a product, preferring one that is in stock", async () => {
    api.getStoreProduct.mockResolvedValue(product({ items: [{ sku: "TEA-S", price: "8.00", currency: "USD", in_stock: false }, { sku: "TEA-L", price: "15.00", currency: "USD", in_stock: true }] }));
    renderInStore(await ProductPage({ params }));
    expect(screen.getByRole("radio", { name: /TEA-L/ })).toBeChecked();
    expect(screen.getByRole("radio", { name: /TEA-S.*out of stock/ })).not.toBeChecked();
    expect(screen.getAllByText("$15.00").length).toBeGreaterThan(0);
    expect(screen.getByText("In stock")).toBeInTheDocument();
  });
});

describe("store layout", () => {
  it("shows the basket, and only notices that are already true", async () => {
    render(await StoreLayout({ children: <p>page body</p> }));
    expect(screen.getByText("page body")).toBeInTheDocument();
    const foot = screen.getByRole("contentinfo");
    expect(foot).toHaveTextContent("continental United States");
    expect(foot).toHaveTextContent("Sales tax is not collected");
    expect(foot).toHaveTextContent("Flat-rate shipping: $5.00");
    expect(screen.getAllByText("Flat-rate shipping: $5.00. Free on orders of $50.00 or more.")).toHaveLength(2);   // the bar above the header and the footer
    expect(screen.getByRole("link", { name: "Clean Room" })).toHaveAttribute("href", "/store");
    expect(within(foot).getByRole("link", { name: "info@cleanroom.com" })).toHaveAttribute("href", "mailto:info@cleanroom.com");
    expect(document.querySelector(".cleanroom-theme")).not.toBeNull();
    // no invented policy: nothing about delivery times or returns
    expect(foot.textContent).not.toMatch(/return|refund|business days|guarantee|within \d/i);
  });

  it("explains when the store cannot be reached", async () => {
    api.getStoreInfo.mockRejectedValue(new Error("down"));
    render(await StoreLayout({ children: <p>page body</p> }));
    expect(screen.getByRole("alert")).toHaveTextContent("We cannot reach the store right now");
    expect(screen.queryByText("page body")).not.toBeInTheDocument();
  });
});

describe("metadata", () => {
  it("names the store on its own pages without the platform's suffix", () => {
    expect(metadata.title).toEqual({ absolute: "Clean Room", template: "%s · Clean Room" });
    expect(metadata.openGraph).toMatchObject({ siteName: "Clean Room", url: "https://www.cleanroom.com" });
  });
  it("titles a product page with the product's name", async () => {
    expect(await generateMetadata({ params: Promise.resolve({ slug: "green-tea" }) })).toMatchObject({ title: "Green Tea", description: "Loose leaf green tea." });
  });
  it("copes with a missing product", async () => {
    api.getStoreProduct.mockResolvedValue(null);
    expect(await generateMetadata({ params: Promise.resolve({ slug: "nope" }) })).toEqual({ title: "Product" });
  });
});

describe("product page states", () => {
  it("is a 404 for a product that is not on sale", async () => {
    api.getStoreProduct.mockResolvedValue(null);
    await expect(ProductPage({ params: Promise.resolve({ slug: "nope" }) })).rejects.toThrow();
  });
  it("shows the closed page when the store is closed", async () => {
    api.getStoreInfo.mockResolvedValue(null);
    render(await ProductPage({ params: Promise.resolve({ slug: "green-tea" }) }));
    expect(screen.getByText("The store is not open yet")).toBeInTheDocument();
  });
});
