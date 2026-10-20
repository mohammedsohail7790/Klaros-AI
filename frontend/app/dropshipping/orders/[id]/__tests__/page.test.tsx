import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { order } from "../../../__tests__/fixtures";

const api = vi.hoisted(() => ({ getOrder: vi.fn(), markOrderPaid: vi.fn(), cancelOrder: vi.fn(), requestRefund: vi.fn(), erasePersonalData: vi.fn() }));
const who = vi.hoisted(() => ({ role: "OWNER" }));
vi.mock("next/navigation", () => ({ useParams: () => ({ id: "o1" }) }));
vi.mock("@/lib/useAuth", () => ({ useAuth: () => ({ token: "t", user: { id: "u", role: who.role }, loading: false }) }));
vi.mock("@/components/AppShell", () => ({ default: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock("@/lib/dropshippingApi", async () => ({ ...(await vi.importActual<object>("@/lib/dropshippingApi")), ...api }));
import { ApiError } from "@/lib/api";
import Page from "@/app/dropshipping/orders/[id]/page";

const buttons = () => screen.queryAllByRole("button").map((b) => b.textContent);

beforeEach(() => {
  who.role = "OWNER";
  Object.values(api).forEach((f) => f.mockReset());
  api.getOrder.mockResolvedValue({ order: order({ status: "PENDING_PAYMENT", paid_at: null, payment_source: null, payment_reference: null, supplier_orders: [] }) });
});

describe("Order detail", () => {
  it("shows the items, the totals the server computed, and who it ships to", async () => {
    api.getOrder.mockResolvedValue({ order: order() });
    render(<Page />);
    expect(await screen.findByRole("heading", { name: "DS-ABCD2345" })).toBeInTheDocument();
    expect(screen.getByText("Green Tea")).toBeInTheDocument();
    expect(screen.getByText("35.00 USD")).toBeInTheDocument();
    expect(screen.getByText(/12 Main St/)).toBeInTheDocument();
    expect(screen.getByText(/Reference: pi_123/)).toBeInTheDocument();
    expect(screen.getByText(/Paid .* by card \(Stripe\)/)).toBeInTheDocument();
    expect(screen.getByText("Acme Supply")).toBeInTheDocument();
  });

  it("offers each role only what it may do to an unpaid order", async () => {
    render(<Page />);
    await screen.findByRole("heading", { name: "DS-ABCD2345" });
    expect(buttons()).toEqual(expect.arrayContaining(["Record a payment", "Cancel the order"]));
    expect(buttons()).not.toContain("Flag a refund");
    expect(buttons()).not.toContain("Erase personal data");
  });

  it("lets staff work an order but not record a payment", async () => {
    who.role = "STAFF";
    render(<Page />);
    await screen.findByRole("heading", { name: "DS-ABCD2345" });
    expect(buttons()).toContain("Cancel the order");
    expect(buttons()).not.toContain("Record a payment");
  });

  it("gives a read-only user no actions at all", async () => {
    who.role = "READ_ONLY";
    render(<Page />);
    await screen.findByRole("heading", { name: "DS-ABCD2345" });
    expect(buttons()).toEqual([]);
  });

  it("requires a payment reference before it records a payment", async () => {
    api.markOrderPaid.mockResolvedValue({ order: order() });
    render(<Page />);
    await userEvent.click(await screen.findByRole("button", { name: "Record a payment" }));
    const confirm = screen.getByRole("button", { name: "Record payment" });
    expect(confirm).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Payment reference"), "BANK-77");
    await userEvent.click(confirm);
    await waitFor(() => expect(api.markOrderPaid).toHaveBeenCalledWith("t", "o1", "BANK-77"));
    expect(await screen.findByText("Paid, order from supplier")).toBeInTheDocument();
  });

  it("warns that cancelling a paid order flags a refund and that Klaros moves no money", async () => {
    api.getOrder.mockResolvedValue({ order: order() });
    api.cancelOrder.mockResolvedValue({ order: order({ status: "REFUND_REQUESTED", status_note: "Cancelled before any supplier order was placed: refund needed. changed mind" }) });
    render(<Page />);
    await userEvent.click(await screen.findByRole("button", { name: "Cancel the order" }));
    expect(screen.getByText(/refund it in Stripe/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Reason"), "changed mind");
    await userEvent.click(screen.getAllByRole("button", { name: "Cancel the order" }).at(-1)!);
    await waitFor(() => expect(api.cancelOrder).toHaveBeenCalledWith("t", "o1", "changed mind"));
    expect(await screen.findByText("Refund needed")).toBeInTheDocument();                 // the status badge
    expect(screen.getByText(/Cancelled before any supplier order was placed/)).toBeInTheDocument();   // and the note
  });

  it("keeps the dialog open and shows the server's reasons when an action is refused", async () => {
    api.cancelOrder.mockRejectedValue(new ApiError(409, "An order that is SHIPPED cannot be cancelled here.", undefined, undefined));
    render(<Page />);
    await userEvent.click(await screen.findByRole("button", { name: "Cancel the order" }));
    await userEvent.type(screen.getByLabelText("Reason"), "x");
    await userEvent.click(screen.getAllByRole("button", { name: "Cancel the order" }).at(-1)!);
    expect(await screen.findByText(/cannot be cancelled here/)).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("offers erasure only once an order is finished, after a warning", async () => {
    api.getOrder.mockResolvedValue({ order: order({ status: "DELIVERED" }) });
    api.erasePersonalData.mockResolvedValue({ order: order({ status: "DELIVERED", personal_data_erased: true, ship_to: null, contact_email: null, contact_phone: null }) });
    render(<Page />);
    await userEvent.click(await screen.findByRole("button", { name: "Erase personal data" }));
    expect(screen.getByText(/cannot be undone/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Erase" }));
    await waitFor(() => expect(api.erasePersonalData).toHaveBeenCalledWith("t", "o1"));
    expect(await screen.findByText("Personal data was erased.")).toBeInTheDocument();
    expect(screen.queryByText(/12 Main St/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Erase personal data" })).not.toBeInTheDocument();
  });

  it("shows a note the system left on the order", async () => {
    api.getOrder.mockResolvedValue({ order: order({ status: "PENDING_PAYMENT", status_note: "Payment of 10.00 received but the order total is 35.00" }) });
    render(<Page />);
    expect(await screen.findByText(/received but the order total is 35.00/)).toBeInTheDocument();
  });
});
