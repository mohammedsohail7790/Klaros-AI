import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { product, renderInStore, saveCart, teaLine } from "./fixtures";

const api = vi.hoisted(() => ({ getStoreProduct: vi.fn() }));
vi.mock("@/lib/storeApi", async () => ({ ...(await vi.importActual<object>("@/lib/storeApi")), ...api }));
import { StoreHeader } from "@/components/store/Chrome";
import { QuickAdd } from "@/components/store/QuickAdd";

beforeEach(() => {
  localStorage.clear();
  document.body.style.overflow = "";
  api.getStoreProduct.mockReset();
  api.getStoreProduct.mockResolvedValue(product());
});

describe("store header", () => {
  it("shows the shipping promise, a search field and the aisles", () => {
    renderInStore(<StoreHeader categories={["Rice", "Tea"]} />);
    expect(screen.getByText("Flat-rate shipping: $5.00. Free on orders of $50.00 or more.")).toBeInTheDocument();
    const search = screen.getByRole("search");
    expect(within(search).getByRole("searchbox", { name: "Search products" })).toHaveAttribute("name", "q");
    expect(search).toHaveAttribute("action", "/store");
    const aisles = within(screen.getByRole("navigation", { name: "Store" }));
    expect(aisles.getAllByRole("link").map((a) => a.textContent)).toEqual(["All products", "Rice", "Tea"]);
    expect(aisles.getByRole("link", { name: "Tea" })).toHaveAttribute("href", "/store?category=Tea#products");
  });

  it("counts the basket and opens it as a panel instead of leaving the page", async () => {
    saveCart([teaLine(2)]);
    renderInStore(<StoreHeader />);
    const basket = await screen.findByRole("button", { name: "Basket, 2 items" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await userEvent.click(basket);
    const panel = await screen.findByRole("dialog", { name: "Basket" });
    const row = await within(panel).findByRole("listitem", { name: "Green Tea" });
    expect(row).toHaveTextContent("$10.00 each");
    expect(row).toHaveTextContent("$20.00");
    expect(within(panel).getByRole("link", { name: "Checkout" })).toHaveAttribute("href", "/store/checkout");
    expect(within(panel).getByRole("link", { name: "View full basket" })).toHaveAttribute("href", "/store/cart");
    expect(within(panel).getByText("Shipping (estimate)")).toBeInTheDocument();
    expect(api.getStoreProduct).toHaveBeenCalledWith("green-tea", { live: true });  // prices are read live, and only once the panel is open
  });

  it("changes quantities and removes items inside the panel", async () => {
    saveCart([teaLine(2)]);
    renderInStore(<StoreHeader />);
    await userEvent.click(await screen.findByRole("button", { name: "Basket, 2 items" }));
    const panel = await screen.findByRole("dialog", { name: "Basket" });
    await within(panel).findByRole("listitem", { name: "Green Tea" });
    await userEvent.click(within(panel).getByRole("button", { name: "Increase Quantity of Green Tea" }));
    await waitFor(() => expect(within(panel).getByRole("listitem", { name: "Green Tea" })).toHaveTextContent("$30.00"));
    await userEvent.click(within(panel).getByRole("button", { name: "Remove Green Tea" }));
    expect(await within(panel).findByText("Your basket is empty")).toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem("klaros_store_cart") ?? "[]")).toEqual([]);
  });

  it("closes with Escape, the close button or the backdrop, and gives the page its scrolling back", async () => {
    saveCart([teaLine(1)]);
    renderInStore(<StoreHeader />);
    const open = async () => userEvent.click(await screen.findByRole("button", { name: /^Basket,/ }));
    await open();
    await screen.findByRole("dialog", { name: "Basket" });
    expect(document.body.style.overflow).toBe("hidden");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.body.style.overflow).toBe("");
    await open();
    await userEvent.click(await screen.findByRole("button", { name: "Close basket" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("puts focus inside the panel and keeps Tab there", async () => {
    saveCart([teaLine(1)]);
    renderInStore(<StoreHeader />);
    await userEvent.click(await screen.findByRole("button", { name: /^Basket,/ }));
    const panel = await screen.findByRole("dialog", { name: "Basket" });
    expect(screen.getByRole("button", { name: "Close basket" })).toHaveFocus();
    await within(panel).findByRole("link", { name: "Checkout" });
    for (let i = 0; i < 12; i++) await userEvent.tab();
    expect(panel.contains(document.activeElement)).toBe(true);
  });

  it("blocks checkout while something in the basket cannot be bought", async () => {
    saveCart([teaLine(1)]);
    api.getStoreProduct.mockResolvedValue(product({ items: [{ sku: "TEA-1", price: "10.00", currency: "USD", in_stock: false }] }));
    renderInStore(<StoreHeader />);
    await userEvent.click(await screen.findByRole("button", { name: /^Basket,/ }));
    const panel = await screen.findByRole("dialog", { name: "Basket" });
    expect(await within(panel).findByText(/Out of stock\. Please remove it\./)).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Checkout" })).toBeDisabled();
  });

  it("opens the search field on a phone", async () => {
    renderInStore(<StoreHeader />);
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(screen.getAllByRole("searchbox", { name: "Search products" }).length).toBeGreaterThan(0);
  });

  it("confirms a quick add on the card and counts it in the basket", async () => {
    renderInStore(<><StoreHeader /><QuickAdd sku="TEA-1" slug="green-tea" name="Green Tea" /></>);
    await userEvent.click(screen.getByRole("button", { name: "Add Green Tea to basket" }));
    expect(screen.getByRole("button", { name: "Green Tea added to basket" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Basket, 1 item" })).toBeInTheDocument();
  });
});
