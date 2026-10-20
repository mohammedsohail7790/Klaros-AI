import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { order } from "./fixtures";

const api = vi.hoisted(() => ({ getStoreOrder: vi.fn(), payStoreOrder: vi.fn() }));
const nav = vi.hoisted(() => ({ hint: null as string | null }));
vi.mock("next/navigation", () => ({ useParams: () => ({ token: "tok-abc" }), useSearchParams: () => new URLSearchParams(nav.hint ? `payment=${nav.hint}` : "") }));
vi.mock("@/lib/storeApi", async () => ({ ...(await vi.importActual<object>("@/lib/storeApi")), ...api }));
import { StoreApiError } from "@/lib/storeApi";
import OrderPage from "@/app/store/orders/[token]/page";

const assign = vi.fn();
beforeEach(() => {
  nav.hint = null;
  assign.mockReset();
  Object.defineProperty(window, "location", { value: { assign }, configurable: true });
  api.getStoreOrder.mockReset().mockResolvedValue(order());
  api.payStoreOrder.mockReset();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("order page", () => {
  it("shows progress in words, the items and the totals the store computed", async () => {
    render(<OrderPage />);
    expect(await screen.findByRole("heading", { name: "Order DS-ABCD2345" })).toBeInTheDocument();
    expect(screen.getByTestId("status")).toHaveTextContent("Awaiting payment");
    expect(screen.getByText("3 × Green Tea")).toBeInTheDocument();
    expect(screen.getByText("$35.00")).toBeInTheDocument();
    expect(api.getStoreOrder).toHaveBeenCalledWith("tok-abc");
  });

  it("shows no personal data (the store never sends any)", async () => {
    render(<OrderPage />);
    await screen.findByRole("heading", { name: /Order/ });
    expect(document.body.textContent).not.toMatch(/@|Main St|Austin|78701|pi_|supplier/i);
  });

  it("lets an unpaid order be paid, which goes to Stripe", async () => {
    api.payStoreOrder.mockResolvedValue({ order_number: "DS-ABCD2345", status_token: "tok-abc", status: "PENDING_PAYMENT", total: "35.00", checkout_url: "https://checkout.stripe.test/pay/cs_2" });
    render(<OrderPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Pay $35.00 now" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://checkout.stripe.test/pay/cs_2"));
  });

  it("shows the store's refusal when paying fails", async () => {
    api.payStoreOrder.mockRejectedValue(new StoreApiError(409, "This order is not waiting for payment."));
    render(<OrderPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Pay/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("not waiting for payment");
  });

  it("offers no payment button once the order is paid, and shows tracking (https links only)", async () => {
    api.getStoreOrder.mockResolvedValue(order({
      status: "SHIPPED", status_label: "Shipped", can_pay: false,
      tracking: [{ carrier: "UPS", tracking_number: "1Z1", tracking_url: "https://ups.example/1Z1", status: "SHIPPED" }, { carrier: "Odd", tracking_number: "BAD1", tracking_url: "javascript:alert(1)", status: "SHIPPED" }],
    }));
    render(<OrderPage />);
    await screen.findByTestId("status");
    expect(screen.queryByRole("button", { name: /Pay/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "1Z1" })).toHaveAttribute("href", "https://ups.example/1Z1");
    expect(screen.queryByRole("link", { name: "BAD1" })).not.toBeInTheDocument();
    expect(screen.getByText(/BAD1/)).toBeInTheDocument();
  });

  it("says plainly when the link does not match an order", async () => {
    api.getStoreOrder.mockRejectedValue(new StoreApiError(404, "Not found"));
    render(<OrderPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("We could not find this order");
  });

  it("explains a cancelled payment without calling it paid", async () => {
    nav.hint = "cancelled";
    render(<OrderPage />);
    expect(await screen.findByText(/No payment was taken/)).toBeInTheDocument();
    expect(screen.getByTestId("status")).toHaveTextContent("Awaiting payment");
  });
});

describe("returning from Stripe (?payment=success is only a hint)", () => {
  beforeEach(() => { nav.hint = "success"; vi.useFakeTimers({ shouldAdvanceTime: true }); });

  it("keeps checking until the store itself says the order is paid", async () => {
    api.getStoreOrder.mockResolvedValueOnce(order()).mockResolvedValueOnce(order()).mockResolvedValue(order({ status: "PAID", status_label: "Paid. We are preparing your order.", can_pay: false }));
    render(<OrderPage />);
    expect(await screen.findByText(/Confirming your payment/)).toBeInTheDocument();
    expect(screen.getByTestId("status")).toHaveTextContent("Awaiting payment");        // not 'paid' just because the URL says so
    await act(async () => { await vi.advanceTimersByTimeAsync(2600); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2600); });
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("Paid. We are preparing your order."));
    expect(screen.queryByText(/Confirming your payment/)).not.toBeInTheDocument();
    expect(api.getStoreOrder).toHaveBeenCalledTimes(3);
  });

  it("stops after a few tries and says confirmation has not arrived, without claiming it was paid", async () => {
    render(<OrderPage />);
    await screen.findByText(/Confirming your payment/);
    for (let i = 0; i < 6; i++) await act(async () => { await vi.advanceTimersByTimeAsync(2600); });
    expect(await screen.findByText(/not received confirmation of your payment yet/)).toBeInTheDocument();
    expect(screen.getByTestId("status")).toHaveTextContent("Awaiting payment");
    expect(api.getStoreOrder).toHaveBeenCalledTimes(6);        // the first load plus five checks
  });
});
