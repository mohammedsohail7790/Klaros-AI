import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CART_KEY, product, renderInStore, saveCart, teaLine } from "./fixtures";

const api = vi.hoisted(() => ({ getStoreProduct: vi.fn(), createCheckout: vi.fn() }));
vi.mock("@/lib/storeApi", async () => ({ ...(await vi.importActual<object>("@/lib/storeApi")), ...api }));
import { StoreApiError } from "@/lib/storeApi";
import CheckoutPage from "@/app/store/checkout/page";

const assign = vi.fn();
const RESULT = { order_number: "DS-ABCD2345", status_token: "tok-abc", status: "PENDING_PAYMENT", total: "25.00", checkout_url: "https://checkout.stripe.test/pay/cs_1" };

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  assign.mockReset();
  Object.defineProperty(window, "location", { value: { assign }, configurable: true });
  api.getStoreProduct.mockReset().mockResolvedValue(product());
  api.createCheckout.mockReset().mockResolvedValue(RESULT);
  saveCart([teaLine(2)]);
});

async function fill(over: Partial<Record<string, string>> = {}) {
  const v = { name: "Pat Shopper", email: "pat@example.com", line1: "12 Main St", city: "Austin", zip: "78701", ...over };
  await userEvent.type(await screen.findByLabelText("Full name"), v.name!);
  await userEvent.type(screen.getByLabelText("Email"), v.email!);
  await userEvent.type(screen.getByLabelText("Street address"), v.line1!);
  await userEvent.type(screen.getByLabelText("City"), v.city!);
  await userEvent.selectOptions(screen.getByLabelText("State"), over.state ?? "TX");
  await userEvent.type(screen.getByLabelText("ZIP code"), v.zip!);
}
const pay = () => userEvent.click(screen.getByRole("button", { name: "Pay with card" }));

describe("checkout", () => {
  it("lists only the states we deliver to", async () => {
    renderInStore(<CheckoutPage />);
    const select = await screen.findByLabelText("State");
    const codes = Array.from((select as HTMLSelectElement).options).map((o) => o.value).filter(Boolean);
    expect(codes).toHaveLength(49);
    expect(codes).toContain("DC");
    expect(codes).not.toContain("AK");
    expect(codes).not.toContain("HI");
  });

  it("asks for what is missing before it contacts the store", async () => {
    renderInStore(<CheckoutPage />);
    await screen.findByLabelText("Full name");
    await pay();
    const alert = screen.getByRole("alert");
    for (const need of ["your name", "valid email", "street address", "your city", "your state", "5-digit ZIP"]) expect(alert).toHaveTextContent(need);
    expect(api.createCheckout).not.toHaveBeenCalled();
  });

  it("checks the phone number only if one is given", async () => {
    renderInStore(<CheckoutPage />);
    await fill();
    await userEvent.type(screen.getByLabelText("Phone (optional)"), "555");
    await pay();
    expect(screen.getByRole("alert")).toHaveTextContent("10 digits");
    expect(api.createCheckout).not.toHaveBeenCalled();
  });

  it("sends only items, contact, address and a key, then clears the basket and goes to Stripe", async () => {
    renderInStore(<CheckoutPage />);
    await fill();
    await pay();
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://checkout.stripe.test/pay/cs_1"));
    const body = api.createCheckout.mock.calls[0][0];
    expect(Object.keys(body).sort()).toEqual(["contact", "idempotency_key", "lines", "ship_to"]);       // no price, total or shipping can be offered
    expect(body.lines).toEqual([{ sku: "TEA-1", quantity: 2 }]);
    expect(body.ship_to).toMatchObject({ line1: "12 Main St", city: "Austin", state: "TX", postal_code: "78701" });
    expect(JSON.parse(localStorage.getItem(CART_KEY)!)).toEqual([]);
    expect(JSON.parse(localStorage.getItem("klaros_store_last_order")!)).toEqual({ token: "tok-abc", orderNumber: "DS-ABCD2345" });
  });

  it("reuses the key for the same basket after a failure, so a retry cannot create a second order", async () => {
    api.createCheckout.mockRejectedValueOnce(new StoreApiError(502, "Payment provider error")).mockResolvedValue(RESULT);
    renderInStore(<CheckoutPage />);
    await fill();
    await pay();
    expect(await screen.findByRole("alert")).toHaveTextContent("Payment provider error");
    expect(JSON.parse(localStorage.getItem(CART_KEY)!)).toHaveLength(1);                              // the basket is kept when it failed
    await pay();
    await waitFor(() => expect(assign).toHaveBeenCalled());
    expect(api.createCheckout.mock.calls[1][0].idempotency_key).toBe(api.createCheckout.mock.calls[0][0].idempotency_key);
  });

  it("shows the store's reasons in words", async () => {
    api.createCheckout.mockRejectedValue(new StoreApiError(422, "The request was refused.", ["ship_to.state must be one of the 48 contiguous states", "TEA-1: this item is not available right now"]));
    renderInStore(<CheckoutPage />);
    await fill();
    await pay();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Delivery address: state must be one of the 48 contiguous states");
    expect(alert).toHaveTextContent("TEA-1: this item is not available right now");
    expect(assign).not.toHaveBeenCalled();
  });

  it("goes to the order page when the order is already paid (no Stripe session)", async () => {
    api.createCheckout.mockResolvedValue({ ...RESULT, status: "PAID", checkout_url: null });
    renderInStore(<CheckoutPage />);
    await fill();
    await pay();
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/store/orders/tok-abc"));
  });

  it("will not start with an empty or unbuyable basket", async () => {
    localStorage.clear();
    renderInStore(<CheckoutPage />);
    expect(await screen.findByText(/Your basket is empty/)).toBeInTheDocument();
  });

  it("sends a sold-out basket back to the basket", async () => {
    api.getStoreProduct.mockResolvedValue(product({ items: [{ sku: "TEA-1", price: "10.00", currency: "USD", in_stock: false }] }));
    renderInStore(<CheckoutPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("not available");
    expect(screen.queryByLabelText("Full name")).not.toBeInTheDocument();
  });

  it("states that card details are never typed here", async () => {
    renderInStore(<CheckoutPage />);
    expect(await screen.findByText(/we never see your card/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/card number/i)).not.toBeInTheDocument();
  });
});
