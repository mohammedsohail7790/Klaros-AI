import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { order } from "./fixtures";

const api = vi.hoisted(() => ({ getStoreOrder: vi.fn(), payStoreOrder: vi.fn() }));
vi.mock("next/navigation", () => ({ useParams: () => ({ token: "tok-abc" }), useSearchParams: () => new URLSearchParams("") }));
vi.mock("@/lib/storeApi", async () => ({ ...(await vi.importActual<object>("@/lib/storeApi")), ...api }));
import OrderPage from "@/app/store/orders/[token]/page";

beforeEach(() => api.getStoreOrder.mockReset());
const stepsOf = () => within(screen.getByRole("list", { name: "Order progress" })).getAllByRole("listitem");

describe("order progress", () => {
  it("shows how far the order has got and what comes next", async () => {
    api.getStoreOrder.mockResolvedValue(order({ status: "SUPPLIER_ORDER_PLACED", status_label: "Your order has been sent to our supplier.", can_pay: false }));
    render(<OrderPage />);
    await screen.findByTestId("status");
    const steps = stepsOf();
    expect(steps.map((s) => s.textContent)).toEqual(["Order placeddone", "Payment receiveddone", "Being prepareddone", "4Shippednext", "5Deliveredto come"]);
    expect(steps[3]).toHaveAttribute("aria-current", "step");
  });

  it("starts at 'order placed' while the order awaits payment, and offers payment", async () => {
    api.getStoreOrder.mockResolvedValue(order());
    render(<OrderPage />);
    await screen.findByTestId("status");
    expect(stepsOf().map((s) => s.textContent)).toEqual(["Order placeddone", "2Payment receivednext", "3Being preparedto come", "4Shippedto come", "5Deliveredto come"]);
    expect(screen.getByText("This order is waiting for payment.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pay $35.00 now" })).toBeEnabled();
  });

  it("completes every step once delivered", async () => {
    api.getStoreOrder.mockResolvedValue(order({ status: "DELIVERED", status_label: "Delivered", can_pay: false }));
    render(<OrderPage />);
    await screen.findByTestId("status");
    expect(stepsOf().every((s) => s.textContent!.endsWith("done"))).toBe(true);
  });

  it("shows a cancelled order as a notice, not as progress", async () => {
    api.getStoreOrder.mockResolvedValue(order({ status: "CANCELLED", status_label: "Cancelled", can_pay: false }));
    render(<OrderPage />);
    await screen.findByTestId("status");
    expect(screen.queryByRole("list", { name: "Order progress" })).not.toBeInTheDocument();
    expect(screen.getAllByText("Cancelled").length).toBeGreaterThan(0);
  });
});
