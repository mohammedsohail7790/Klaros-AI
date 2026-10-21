import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { INFO, product, renderInStore } from "./fixtures";

const api = vi.hoisted(() => ({ getStoreInfo: vi.fn(), listStoreProducts: vi.fn(), listAllStoreProducts: vi.fn(), getStoreProduct: vi.fn() }));
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
  api.listAllStoreProducts.mockResolvedValue([product()]);
  api.getStoreProduct.mockResolvedValue(product());
});

describe("store home (server-rendered)", () => {
  const names = () => [...screen.getByRole("region", { name: "Products" }).querySelectorAll("ul.grid > li h3")].map((h) => h.textContent);
  const priced = (name: string, slug: string, category: string, price: string, in_stock = true) => product({ name, slug, category, description: null, items: [{ sku: slug.toUpperCase(), price, currency: "USD", in_stock }] });

  it("lists products with price and stock", async () => {
    api.listAllStoreProducts.mockResolvedValue([product(), product({ name: "Gone Jam", slug: "gone-jam", items: [{ sku: "J", price: "7.00", currency: "USD", in_stock: false }] })]);
    renderInStore(await StoreHome({ searchParams: sp() }));
    const grid = within(screen.getByRole("region", { name: "Products" }));
    expect(grid.getByRole("link", { name: /Green Tea/ }).closest("li")).toHaveTextContent("$10.00");
    expect(grid.getByRole("link", { name: /Gone Jam/ }).closest("li")).toHaveTextContent("Sold out");
    expect(grid.getByRole("link", { name: /Green Tea/ })).toHaveAttribute("href", "/store/products/green-tea");
    expect(grid.getByRole("button", { name: "Add Green Tea to basket" })).toBeInTheDocument();         // one tap to add
    expect(grid.queryByRole("button", { name: /Add Gone Jam/ })).not.toBeInTheDocument();             // a sold-out item cannot be added
    expect(grid.getByRole("link", { name: /Green Tea/ }).closest("li")).toHaveTextContent("Shelf-stable");
  });

  it("reads the whole catalog once and narrows it by search, aisle, stock and price", async () => {
    api.listAllStoreProducts.mockResolvedValue([priced("Green Tea", "tea-g", "Tea", "10.00"), priced("Black Tea", "tea-b", "Tea", "4.00"), priced("Old Tea", "tea-o", "Tea", "6.00", false), priced("Brown Rice", "rice", "Rice", "3.00")]);
    renderInStore(await StoreHome({ searchParams: sp({ q: "tea" }) }));
    expect(api.listAllStoreProducts).toHaveBeenCalledTimes(1);   // the whole catalog, read once
    expect(names().sort()).toEqual(["Black Tea", "Green Tea", "Old Tea"]);
    expect(screen.getByRole("heading", { name: "Results for “tea”" })).toBeInTheDocument();
    expect(screen.getByText("3 products")).toBeInTheDocument();
  });

  it("filters by aisle, in-stock and price band, and every filter link keeps the others", async () => {
    api.listAllStoreProducts.mockResolvedValue([priced("Green Tea", "tea-g", "Tea", "10.00"), priced("Black Tea", "tea-b", "Tea", "4.00"), priced("Old Tea", "tea-o", "Tea", "6.00", false), priced("Brown Rice", "rice", "Rice", "3.00")]);
    renderInStore(await StoreHome({ searchParams: sp({ category: "Tea", stock: "1" }) }));
    expect(names().sort()).toEqual(["Black Tea", "Green Tea"]);                                      // the sold-out tea and the rice are out
    const filters = within(screen.getByRole("complementary", { name: "Filters" }));
    expect(filters.getByRole("link", { name: "Under $5" })).toHaveAttribute("href", "/store?category=Tea&stock=1&price=u5#products");
    expect(filters.getByRole("link", { name: /Rice/ })).toHaveAttribute("href", "/store?category=Rice&stock=1#products");
    expect(filters.getByRole("link", { name: /In stock only/ })).toHaveAttribute("href", "/store?category=Tea#products");  // turns it off
    const applied = within(screen.getByRole("list", { name: "Applied filters" }));
    expect(applied.getByRole("link", { name: "Remove Tea" })).toHaveAttribute("href", "/store?stock=1#products");
    expect(applied.getByRole("link", { name: "Remove In stock only" })).toHaveAttribute("href", "/store?category=Tea#products");
  });

  it("shows aisle counts that match what each aisle would list", async () => {
    api.listAllStoreProducts.mockResolvedValue(many(5));
    renderInStore(await StoreHome({ searchParams: sp() }));
    const aisles = within(screen.getByRole("complementary", { name: "Filters" }).querySelector("section[aria-label='Aisle']") as HTMLElement);
    expect(aisles.getByRole("link", { name: /All aisles/ })).toHaveTextContent("5");
    expect(aisles.getByRole("link", { name: /Rice/ })).toHaveTextContent("3");
    expect(aisles.getByRole("link", { name: /Tea/ })).toHaveTextContent("2");
  });

  it("sorts, and marks the current order", async () => {
    api.listAllStoreProducts.mockResolvedValue([priced("Mid", "m", "A", "5.00"), priced("Cheap", "c", "A", "1.00"), priced("Dear", "d", "A", "9.00")]);
    renderInStore(await StoreHome({ searchParams: sp({ sort: "price-asc" }) }));
    expect(names()).toEqual(["Cheap", "Mid", "Dear"]);
    const sort = within(screen.getByRole("navigation", { name: "Sort" }));
    expect(sort.getByRole("link", { name: "Price: low to high" })).toHaveAttribute("aria-current", "true");
    expect(sort.getByRole("link", { name: "Name: A to Z" })).toHaveAttribute("href", "/store?sort=name#products");
  });

  it("pages through a big catalog 24 at a time", async () => {
    api.listAllStoreProducts.mockResolvedValue(many(30));
    renderInStore(await StoreHome({ searchParams: sp() }));
    expect(names()).toHaveLength(24);
    expect(screen.getByRole("link", { name: /Next page/ })).toHaveAttribute("href", "/store?page=2#products");
    expect(screen.queryByRole("link", { name: /Previous page/ })).not.toBeInTheDocument();
  });

  it("shows the second page with a way back", async () => {
    api.listAllStoreProducts.mockResolvedValue(many(30));
    renderInStore(await StoreHome({ searchParams: sp({ page: "2", category: "Tea" }) }));
    expect(screen.getByRole("link", { name: /Previous page/ })).toHaveAttribute("href", "/store?category=Tea#products");
    expect(screen.queryByRole("link", { name: /Next page/ })).not.toBeInTheDocument();
  });

  it("opens with the offers, the aisles with real counts, what is in stock now and the trust facts", async () => {
    api.listAllStoreProducts.mockResolvedValue(many(5));
    renderInStore(await StoreHome({ searchParams: sp() }));
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Pantry staples, delivered.");
    expect(screen.getByText("Free shipping from $50.00")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Why shop here" })).toHaveTextContent("We cannot ship to Alaska, Hawaii or US territories.");
    const tiles = within(screen.getByRole("region", { name: "Shop by category" }));
    expect(tiles.getByRole("link", { name: "Shop Rice" })).toHaveTextContent("3 products");
    expect(tiles.getByRole("link", { name: "Shop Tea" })).toHaveAttribute("href", "/store?category=Tea#products");
    expect(within(screen.getByRole("region", { name: "In stock now" })).getAllByRole("listitem").length).toBeGreaterThan(0);
  });

  it("drops the offers, aisles and rail when the shopper is searching or filtering", async () => {
    renderInStore(await StoreHome({ searchParams: sp({ q: "tea" }) }));
    for (const name of ["Welcome", "Shop by category", "In stock now"]) expect(screen.queryByRole("region", { name })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Remove “tea”" })).toHaveAttribute("href", "/store#products");
  });

  it("says plainly when nothing matches and how to get back", async () => {
    api.listAllStoreProducts.mockResolvedValue([]);
    renderInStore(await StoreHome({ searchParams: sp({ q: "zzz" }) }));
    expect(screen.getByText("Nothing matches that.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Clear search and filters" })).toHaveAttribute("href", "/store#products");
  });

  it("shows a closed-store page rather than an error when the store is closed", async () => {
    api.getStoreInfo.mockResolvedValue(null);
    renderInStore(await StoreHome({ searchParams: sp() }));
    expect(screen.getByText("The store is not open yet")).toBeInTheDocument();
    expect(api.listAllStoreProducts).not.toHaveBeenCalled();
  });

  it("shows 'cannot reach the store' when the API is down, and never crashes", async () => {
    api.listAllStoreProducts.mockRejectedValue(new Error("down"));
    renderInStore(await StoreHome({ searchParams: sp() }));
    expect(screen.getByRole("alert")).toHaveTextContent("We cannot reach the store right now");
  });

  it("ignores junk in the page, sort, price and search parameters", async () => {
    api.listAllStoreProducts.mockResolvedValue(many(3));
    renderInStore(await StoreHome({ searchParams: sp({ page: "abc", sort: "evil", price: "zzz", stock: "maybe", q: "x".repeat(500) }) }));
    expect(screen.getByRole("heading", { name: /Results for/ }).textContent!.length).toBeLessThan(100);
    expect(screen.getByRole("link", { name: "Featured" })).toHaveAttribute("aria-current", "true");
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
    expect(screen.getByRole("link", { name: "Clean Room" })).toHaveAttribute("href", "/store/welcome");
    expect(within(foot).getByRole("link", { name: "info@yourcleanroom.com" })).toHaveAttribute("href", "mailto:info@yourcleanroom.com");
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
    expect(metadata.openGraph).toMatchObject({ siteName: "Clean Room", url: "https://www.yourcleanroom.com" });
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
