import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CART_KEY, product, renderInStore, saveCart, teaLine } from "./fixtures";

const api = vi.hoisted(() => ({ getStoreProduct: vi.fn() }));
vi.mock("@/lib/storeApi", async () => ({ ...(await vi.importActual<object>("@/lib/storeApi")), ...api }));
import CartPage from "@/app/store/cart/page";
import { AddToCart } from "@/components/store/AddToCart";

beforeEach(() => {
  localStorage.clear();
  api.getStoreProduct.mockReset();
  api.getStoreProduct.mockResolvedValue(product());
});

describe("basket page", () => {
  it("shows live prices, an estimate, and a way to check out", async () => {
    saveCart([teaLine(2)]);
    renderInStore(<CartPage />);
    const row = await screen.findByRole("listitem", { name: "Green Tea" });
    expect(row).toHaveTextContent("$10.00 each");
    expect(row).toHaveTextContent("$20.00");
    expect(screen.getByText("$5.00")).toBeInTheDocument();                       // shipping estimate
    expect(screen.getByText("$25.00")).toBeInTheDocument();                      // estimated total
    expect(screen.getByText(/final total is confirmed when you check out/)).toBeInTheDocument();
    expect(screen.getByText(/Sales tax is not collected/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Checkout" })).toHaveAttribute("href", "/store/checkout");
    expect(api.getStoreProduct).toHaveBeenCalledWith("green-tea", { live: true });  // read live, never from the cache
  });

  it("applies free shipping from the threshold", async () => {
    saveCart([teaLine(5)]);
    renderInStore(<CartPage />);
    await screen.findByRole("listitem", { name: "Green Tea" });
    expect(screen.getAllByText("$50.00")).toHaveLength(3);                       // the line, the subtotal and the total
    expect(screen.getByText("$0.00")).toBeInTheDocument();                       // shipping
  });

  it("lets a quantity be cleared and retyped without losing the item", async () => {
    saveCart([teaLine(2)]);
    renderInStore(<CartPage />);
    const qty = await screen.findByLabelText("Quantity of Green Tea");
    await userEvent.clear(qty);
    expect(JSON.parse(localStorage.getItem(CART_KEY)!)).toHaveLength(1);           // an empty box changes nothing
    await userEvent.type(qty, "0");
    expect(JSON.parse(localStorage.getItem(CART_KEY)!)[0].quantity).toBe(2);       // 0 is out of range and ignored
    await userEvent.type(qty, "{Backspace}500");                                   // passes through 5 and 50, then 500 is ignored
    expect(JSON.parse(localStorage.getItem(CART_KEY)!)[0].quantity).toBe(50);
    await userEvent.click(document.body);
    expect(qty).toHaveValue(50);                                                   // leaving the box shows the real quantity, never the rejected 500
  });

  it("changes quantity and removes a line, and remembers the basket", async () => {
    saveCart([teaLine(2)]);
    renderInStore(<CartPage />);
    const qty = await screen.findByLabelText("Quantity of Green Tea");
    await userEvent.clear(qty);
    await userEvent.type(qty, "3");
    await waitFor(() => expect(JSON.parse(localStorage.getItem(CART_KEY)!)[0].quantity).toBe(3));
    await userEvent.click(screen.getByRole("button", { name: "Remove Green Tea" }));
    expect(await screen.findByText("Your basket is empty")).toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem(CART_KEY)!)).toEqual([]);
  });

  it("flags a sold-out item and an item the store no longer has, and blocks checkout", async () => {
    saveCart([teaLine(1), { sku: "RICE-1", slug: "brown-rice", name: "Brown Rice", quantity: 1 }]);
    api.getStoreProduct.mockImplementation((slug: string) => Promise.resolve(slug === "green-tea" ? product({ items: [{ sku: "TEA-1", price: "10.00", currency: "USD", in_stock: false }] }) : null));
    renderInStore(<CartPage />);
    expect(await screen.findByText(/Out of stock\. Please remove it/)).toBeInTheDocument();
    expect(screen.getByText(/no longer available/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Checkout" })).toBeDisabled();
    expect(screen.queryByRole("link", { name: "Checkout" })).not.toBeInTheDocument();
  });

  it("uses today's price even if the saved basket remembered another", async () => {
    saveCart([{ ...teaLine(1), price: "1.00" }]);
    api.getStoreProduct.mockResolvedValue(product({ items: [{ sku: "TEA-1", price: "12.00", currency: "USD", in_stock: true }] }));
    renderInStore(<CartPage />);
    expect(await screen.findByRole("listitem", { name: "Green Tea" })).toHaveTextContent("$12.00 each");
  });

  it("offers the last order when the basket is empty", async () => {
    localStorage.setItem("klaros_store_last_order", JSON.stringify({ token: "tok9", orderNumber: "DS-LAST2222" }));
    renderInStore(<CartPage />);
    expect(await screen.findByRole("link", { name: /DS-LAST2222/ })).toHaveAttribute("href", "/store/orders/tok9");
  });

  it("shows the closed page when the store is closed", async () => {
    renderInStore(<CartPage />, null);
    expect(await screen.findByText("The store is not open yet")).toBeInTheDocument();
  });
});

describe("add to basket", () => {
  it("adds the chosen quantity and confirms it", async () => {
    renderInStore(<AddToCart product={product()} />);
    const qty = screen.getByLabelText("Quantity");
    await userEvent.clear(qty);
    await userEvent.type(qty, "4");
    await userEvent.click(screen.getByRole("button", { name: "Add to basket" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Added");
    await waitFor(() => expect(JSON.parse(localStorage.getItem(CART_KEY)!)).toEqual([{ sku: "TEA-1", slug: "green-tea", name: "Green Tea", quantity: 4 }]));
    expect(within(screen.getByRole("status")).getByRole("link", { name: "View basket" })).toHaveAttribute("href", "/store/cart");
  });
});
