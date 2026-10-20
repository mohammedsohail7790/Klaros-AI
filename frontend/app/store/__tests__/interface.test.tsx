import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CART_KEY, INFO, product, renderInStore, saveCart, teaLine } from "./fixtures";

const api = vi.hoisted(() => ({ getStoreProduct: vi.fn() }));
vi.mock("@/lib/storeApi", async () => ({ ...(await vi.importActual<object>("@/lib/storeApi")), ...api }));
import CartPage from "@/app/store/cart/page";
import { ProductCard } from "@/components/store/parts";

beforeEach(() => {
  localStorage.clear();
  api.getStoreProduct.mockReset().mockResolvedValue(product());
});

describe("quantity stepper", () => {
  it("steps the quantity with minus and plus, and stops at the lower limit", async () => {
    saveCart([teaLine(1)]);
    renderInStore(<CartPage />);
    const minus = await screen.findByRole("button", { name: "Decrease Quantity of Green Tea" });
    expect(minus).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Increase Quantity of Green Tea" }));
    await waitFor(() => expect(JSON.parse(localStorage.getItem(CART_KEY)!)[0].quantity).toBe(2));
    await userEvent.click(screen.getByRole("button", { name: "Decrease Quantity of Green Tea" }));
    await waitFor(() => expect(JSON.parse(localStorage.getItem(CART_KEY)!)[0].quantity).toBe(1));
  });
});

describe("free-shipping meter", () => {
  it("shows how far the basket is from free shipping, from the owner's own threshold", async () => {
    saveCart([teaLine(2)]);                                                       // $20.00 of the $50.00 needed
    renderInStore(<CartPage />);
    expect(await screen.findByText(/more for free shipping/)).toHaveTextContent("Add $30.00 more for free shipping.");
    expect(screen.getByRole("progressbar", { name: "Progress to free shipping" })).toHaveAttribute("aria-valuenow", "40");
  });

  it("says so once free shipping is earned", async () => {
    saveCart([teaLine(5)]);
    renderInStore(<CartPage />);
    expect(await screen.findByText("You have free shipping.")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
  });

  it("shows no meter when the store has no free-shipping threshold", async () => {
    saveCart([teaLine(2)]);
    renderInStore(<CartPage />, { ...INFO, free_shipping_over: null });
    await screen.findByRole("listitem", { name: "Green Tea" });
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });
});

describe("quick add from the shop", () => {
  it("adds one with a single tap and confirms on the button itself", async () => {
    renderInStore(<ul><ProductCard product={product()} /></ul>);
    await userEvent.click(screen.getByRole("button", { name: "Add Green Tea to basket" }));
    expect(await screen.findByRole("button", { name: "Green Tea added to basket" })).toHaveTextContent("Added");
    expect(JSON.parse(localStorage.getItem(CART_KEY)!)).toEqual([{ sku: "TEA-1", slug: "green-tea", name: "Green Tea", quantity: 1 }]);
  });

  it("sends a product with several options to its page instead of guessing", () => {
    renderInStore(<ul><ProductCard product={product({ items: [{ sku: "S", price: "5.00", currency: "USD", in_stock: true }, { sku: "L", price: "9.00", currency: "USD", in_stock: true }] })} /></ul>);
    expect(screen.queryByRole("button", { name: /Add Green Tea/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Choose" })).toHaveAttribute("href", "/store/products/green-tea");
    expect(screen.getByText("From")).toBeInTheDocument();
  });
});
