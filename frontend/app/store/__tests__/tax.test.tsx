import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { INFO, order, product, renderInStore, saveCart, teaLine } from "./fixtures";

const api = vi.hoisted(() => ({ getStoreProduct: vi.fn(), getStoreOrder: vi.fn() }));
vi.mock("next/navigation", () => ({ useParams: () => ({ token: "tok-abc" }), useSearchParams: () => new URLSearchParams("") }));
vi.mock("@/lib/storeApi", async () => ({ ...(await vi.importActual<object>("@/lib/storeApi")), ...api }));
import CartPage from "@/app/store/cart/page";
import OrderPage from "@/app/store/orders/[token]/page";

const STRIPE_INFO = { ...INFO, sales_tax_mode: "STRIPE_TAX" as const, sales_tax_notice: "Sales tax is calculated at checkout from your delivery address." };

beforeEach(() => {
  localStorage.clear();
  api.getStoreProduct.mockReset().mockResolvedValue(product());
  api.getStoreOrder.mockReset().mockResolvedValue(order());
});

describe("sales tax in the shop", () => {
  it("tells the shopper tax is added at checkout, and that the total shown is before tax", async () => {
    saveCart([teaLine(2)]);
    renderInStore(<CartPage />, STRIPE_INFO);
    const row = await screen.findByRole("listitem", { name: "Green Tea" });
    expect(row).toHaveTextContent("$20.00");
    const totals = screen.getByText("Estimated total before tax").closest("dl") as HTMLElement;
    expect(within(totals).getByText("Sales tax").nextElementSibling).toHaveTextContent("At checkout");
    expect(within(totals).getByText("$25.00")).toBeInTheDocument();
    expect(screen.getByText(/Sales tax is calculated at checkout from your delivery address\./)).toBeInTheDocument();
  });

  it("leaves the basket as it was when no tax is collected", async () => {
    saveCart([teaLine(2)]);
    renderInStore(<CartPage />, { ...INFO, sales_tax_mode: "NONE" });
    await screen.findByRole("listitem", { name: "Green Tea" });
    expect(screen.getByText("Estimated total")).toBeInTheDocument();
    expect(screen.queryByText("At checkout")).not.toBeInTheDocument();
    expect(screen.getByText(/Sales tax is not collected/)).toBeInTheDocument();
  });

  it("says on an unpaid order that tax is added when it is paid, and shows the tax once it has been", async () => {
    api.getStoreOrder.mockResolvedValue(order({ tax_at_payment: true }));
    const { unmount } = render(<OrderPage />);
    const totals = (await screen.findByRole("heading", { name: "Totals" })).closest("div") as HTMLElement;
    expect(within(totals).getByText("Sales tax").nextElementSibling).toHaveTextContent("Added when you pay");
    unmount();
    api.getStoreOrder.mockResolvedValue(order({ status: "PAID", status_label: "Paid", can_pay: false, tax: "2.89", total: "37.89", tax_at_payment: false }));
    render(<OrderPage />);
    const paid = (await screen.findByRole("heading", { name: "Totals" })).closest("div") as HTMLElement;
    expect(within(paid).getByText("Sales tax").nextElementSibling).toHaveTextContent("$2.89");
    expect(within(paid).getByText("$37.89")).toBeInTheDocument();
  });
});
