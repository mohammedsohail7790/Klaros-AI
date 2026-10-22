import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { work } from "./fixtures";

const api = vi.hoisted(() => ({ listSupplierOrders: vi.fn(), downloadSupplierOrdersCsv: vi.fn() }));
const auth = vi.hoisted(() => ({ role: "OWNER" }));
vi.mock("@/lib/useAuth", () => ({ useAuth: () => ({ token: "t", user: { id: "u", role: auth.role }, loading: false }) }));
vi.mock("@/components/AppShell", () => ({ default: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock("@/lib/dropshippingApi", async () => ({ ...(await vi.importActual<object>("@/lib/dropshippingApi")), ...api }));
import Page from "@/app/dropshipping/fulfilment/page";

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  auth.role = "OWNER";
  api.listSupplierOrders.mockResolvedValue({ supplier_orders: [work()] });
  api.downloadSupplierOrdersCsv.mockResolvedValue(undefined);
});

describe("fulfilment: the file for the supplier", () => {
  it("lets staff download the queue they are looking at", async () => {
    render(<Page />);
    await userEvent.click(await screen.findByRole("button", { name: "Download for the supplier (CSV)" }));
    expect(api.downloadSupplierOrdersCsv).toHaveBeenCalledWith("t", "TO_PLACE");
  });

  it("is not offered to someone who may only look, or when the queue is empty", async () => {
    auth.role = "READ_ONLY";
    const first = render(<Page />);
    await screen.findByLabelText(/Supplier order for/);
    expect(screen.queryByRole("button", { name: /Download for the supplier/ })).not.toBeInTheDocument();
    first.unmount();

    auth.role = "OWNER";
    api.listSupplierOrders.mockResolvedValue({ supplier_orders: [] });
    render(<Page />);
    await screen.findByText("Nothing to order from suppliers right now.");
    expect(screen.queryByRole("button", { name: /Download for the supplier/ })).not.toBeInTheDocument();
  });

  it("says so in plain words when the file cannot be made", async () => {
    api.downloadSupplierOrdersCsv.mockRejectedValue(new Error("boom"));
    render(<Page />);
    await userEvent.click(await screen.findByRole("button", { name: "Download for the supplier (CSV)" }));
    expect(await screen.findByText(/Could not make the file|boom/)).toBeInTheDocument();
  });
});
