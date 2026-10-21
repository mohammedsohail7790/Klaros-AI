import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { INFO, product, renderInStore, saveCart, teaLine } from "./fixtures";

const api = vi.hoisted(() => ({ getStoreProduct: vi.fn() }));
vi.mock("@/lib/storeApi", async () => ({ ...(await vi.importActual<object>("@/lib/storeApi")), ...api }));
import CartPage from "@/app/store/cart/page";

const TIERED = { ...INFO, shipping: "Shipping from $6.95, by weight.", shipping_tiers: [{ max_lb: "1", fee: "6.95" }, { max_lb: "5", fee: "10.95" }, { max_lb: null, fee: "53.95" }], free_shipping_over: null };
const weighed = (grams: number | null) => product({ items: [{ sku: "TEA-1", price: "10.00", currency: "USD", in_stock: true, weight_g: grams }] });

beforeEach(() => {
  localStorage.clear();
  api.getStoreProduct.mockReset();
});

describe("basket with shipping by weight", () => {
  it("charges the tier for the weight of what is in the basket", async () => {
    api.getStoreProduct.mockResolvedValue(weighed(500));
    saveCart([teaLine(2)]);                                         // 1000 g = 2.2 lb: the up-to-5-lb tier
    renderInStore(<CartPage />, TIERED);
    await screen.findByRole("listitem", { name: "Green Tea" });
    const totals = screen.getByText("Estimated total").closest("dl") as HTMLElement;
    expect(within(totals).getByText("Shipping (estimate)").nextElementSibling).toHaveTextContent("$10.95");
    expect(within(totals).getByText("$30.95")).toBeInTheDocument();
  });

  it("says shipping is worked out at checkout when an item has no weight", async () => {
    api.getStoreProduct.mockResolvedValue(weighed(null));
    saveCart([teaLine(2)]);
    renderInStore(<CartPage />, TIERED);
    await screen.findByRole("listitem", { name: "Green Tea" });
    const totals = screen.getByText("Subtotal before shipping and tax").closest("dl") as HTMLElement;
    expect(within(totals).getByText("Shipping (estimate)").nextElementSibling).toHaveTextContent("At checkout");
    expect(screen.getByRole("link", { name: "Checkout" })).toBeInTheDocument();      // the server prices it, so the shopper may continue
  });

  it("stops a basket that is heavier than the store ships and says why", async () => {
    api.getStoreProduct.mockResolvedValue(weighed(500));
    saveCart([teaLine(2)]);
    renderInStore(<CartPage />, { ...TIERED, shipping_tiers: [{ max_lb: "2", fee: "5.00" }] });
    expect(await screen.findByRole("alert")).toHaveTextContent("heavier than we ship in one order");
    expect(screen.getByRole("button", { name: "Checkout" })).toBeDisabled();
  });
});
