import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { order, storeView, work } from "./fixtures";

const api = vi.hoisted(() => ({ listOrders: vi.fn(), listSupplierOrders: vi.fn(), getStoreSettings: vi.fn() }));
vi.mock("@/lib/useAuth", () => ({ useAuth: () => ({ token: "t", user: { id: "u", role: "OWNER" }, loading: false }) }));
vi.mock("@/components/AppShell", () => ({ default: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock("@/lib/dropshippingApi", async () => ({ ...(await vi.importActual<object>("@/lib/dropshippingApi")), ...api }));
import Page from "@/app/dropshipping/page";

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  api.listSupplierOrders.mockResolvedValue({ supplier_orders: [] });
  api.listOrders.mockResolvedValue({ orders: [] });
  api.getStoreSettings.mockResolvedValue(storeView({ open_to_shoppers: true }, { open: true }));
});

describe("Store today", () => {
  it("counts what needs a person and lists it", async () => {
    api.listSupplierOrders.mockResolvedValue({ supplier_orders: [work()] });
    api.listOrders.mockImplementation((_t: string, p: { status?: string }) =>
      Promise.resolve({ orders: p.status === "REFUND_REQUESTED" ? [order({ id: "o2", order_number: "DS-REFUND22", status: "REFUND_REQUESTED", status_note: "Refund needed: damaged" })] : [order({ id: "o3", order_number: "DS-UNPAID33", status: "PENDING_PAYMENT", status_note: "Payment of 10.00 received but the order total is 35.00" })] }));
    render(<Page />);
    const toOrder = await screen.findByRole("region", { name: "To order from suppliers" });
    expect(within(toOrder).getByText("DS-ABCD2345")).toBeInTheDocument();
    expect(within(toOrder).getByText(/2 × Green Tea/)).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Refunds needed" })).getByText(/damaged/)).toBeInTheDocument();
    expect(screen.getByText(/Klaros does not move money/)).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Payment notes" })).getByText("DS-UNPAID33")).toBeInTheDocument();
  });

  it("says plainly when nothing needs doing", async () => {
    render(<Page />);
    expect(await screen.findByText("Nothing needs you right now.")).toBeInTheDocument();
  });

  it("warns that the store is closed and what is still missing", async () => {
    api.getStoreSettings.mockResolvedValue(storeView({ blockers: ["Set a shipping rule (a flat fee, which may be 0)."] }));
    render(<Page />);
    expect(await screen.findByText(/The store is closed to shoppers/)).toHaveTextContent("Set a shipping rule");
  });

  it("keeps working when one list fails to load", async () => {
    api.getStoreSettings.mockRejectedValue(new Error("boom"));
    api.listSupplierOrders.mockResolvedValue({ supplier_orders: [work()] });
    render(<Page />);
    expect(await screen.findByRole("region", { name: "To order from suppliers" })).toBeInTheDocument();
  });

  it("shows an error with a retry when everything fails", async () => {
    Object.values(api).forEach((f) => f.mockRejectedValue(new Error("down")));
    render(<Page />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Unable to load the store.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
