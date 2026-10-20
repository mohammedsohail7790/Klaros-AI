import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { work } from "../../__tests__/fixtures";

const api = vi.hoisted(() => ({ listSupplierOrders: vi.fn(), placeSupplierOrder: vi.fn(), failSupplierOrder: vi.fn(), confirmSupplierOrder: vi.fn(), addShipment: vi.fn(), markShipment: vi.fn() }));
const who = vi.hoisted(() => ({ role: "STAFF" }));
vi.mock("@/lib/useAuth", () => ({ useAuth: () => ({ token: "t", user: { id: "u", role: who.role }, loading: false }) }));
vi.mock("@/components/AppShell", () => ({ default: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock("@/lib/dropshippingApi", async () => ({ ...(await vi.importActual<object>("@/lib/dropshippingApi")), ...api }));
import { ApiError } from "@/lib/api";
import Page from "@/app/dropshipping/fulfilment/page";

beforeEach(() => {
  who.role = "STAFF";
  Object.values(api).forEach((f) => f.mockReset());
  api.listSupplierOrders.mockResolvedValue({ supplier_orders: [work()] });
});

const card = async () => within(await screen.findByRole("listitem", { name: "Supplier order for DS-ABCD2345" }));

describe("Fulfilment", () => {
  it("shows exactly what to order from whom, and where it ships", async () => {
    render(<Page />);
    const c = await card();
    expect(c.getByText("Acme Supply")).toBeInTheDocument();
    expect(c.getByText(/2 × Green Tea/)).toHaveTextContent("supplier code TEA-1-S");
    expect(c.getByText(/12 Main St/)).toBeInTheDocument();
    expect(api.listSupplierOrders).toHaveBeenCalledWith("t", "TO_PLACE");
  });

  it("copies the address for the supplier's order form", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<Page />);
    await userEvent.click((await card()).getByRole("button", { name: "Copy address" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("Pat Shopper\n12 Main St\nAustin, TX 78701"));
  });

  it("will not mark an order as placed without the supplier's own order number", async () => {
    api.placeSupplierOrder.mockResolvedValue({ order: {} });
    render(<Page />);
    await userEvent.click((await card()).getByRole("button", { name: "Mark as ordered" }));
    const go = screen.getAllByRole("button", { name: "Mark as ordered" }).at(-1)!;
    expect(go).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Supplier order number"), "ACME-9001");
    await userEvent.click(go);
    await waitFor(() => expect(api.placeSupplierOrder).toHaveBeenCalledWith("t", "s1", "ACME-9001"));
    await waitFor(() => expect(api.listSupplierOrders).toHaveBeenCalledTimes(2));   // the queue reloads
  });

  it("requires a reason to mark a supplier order failed", async () => {
    api.failSupplierOrder.mockResolvedValue({ order: {} });
    render(<Page />);
    await userEvent.click((await card()).getByRole("button", { name: "Mark as failed" }));
    const go = screen.getAllByRole("button", { name: "Mark as failed" }).at(-1)!;
    expect(go).toBeDisabled();
    await userEvent.type(screen.getByLabelText("What went wrong"), "out of stock at supplier");
    await userEvent.click(go);
    await waitFor(() => expect(api.failSupplierOrder).toHaveBeenCalledWith("t", "s1", "out of stock at supplier"));
  });

  it("adds tracking only with a tracking number", async () => {
    api.listSupplierOrders.mockResolvedValue({ supplier_orders: [work({ status: "PLACED", supplier_reference: "ACME-9001" })] });
    api.addShipment.mockResolvedValue({ order: {} });
    render(<Page />);
    await userEvent.click((await card()).getByRole("button", { name: "Add tracking" }));
    const save = screen.getByRole("button", { name: "Save tracking" });
    expect(save).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Tracking number"), "1Z999AA10123456784");
    await userEvent.type(screen.getByLabelText(/Carrier/), "UPS");
    await userEvent.click(save);
    await waitFor(() => expect(api.addShipment).toHaveBeenCalledWith("t", "s1", { carrier: "UPS", tracking_number: "1Z999AA10123456784", tracking_url: undefined }));
  });

  it("marks a parcel delivered and reports a problem with a note", async () => {
    api.listSupplierOrders.mockResolvedValue({ supplier_orders: [work({ status: "PLACED", shipments: [{ id: "sh1", carrier: "UPS", tracking_number: "1Z1", status: "SHIPPED" }] })] });
    api.markShipment.mockResolvedValue({ order: {} });
    render(<Page />);
    await userEvent.click((await card()).getByRole("button", { name: "Mark delivered" }));
    await userEvent.click(screen.getAllByRole("button", { name: "Mark delivered" }).at(-1)!);
    await waitFor(() => expect(api.markShipment).toHaveBeenCalledWith("t", "sh1", "delivered"));
    await userEvent.click((await card()).getByRole("button", { name: "Report a problem" }));
    expect(screen.getByRole("button", { name: "Report" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("What happened"), "lost in transit");
    await userEvent.click(screen.getByRole("button", { name: "Report" }));
    await waitFor(() => expect(api.markShipment).toHaveBeenCalledWith("t", "sh1", "exception", "lost in transit"));
  });

  it("shows the server's refusal and keeps the dialog open", async () => {
    api.placeSupplierOrder.mockRejectedValue(new ApiError(409, "A supplier order that is PLACED cannot be marked placed.", undefined, undefined));
    render(<Page />);
    await userEvent.click((await card()).getByRole("button", { name: "Mark as ordered" }));
    await userEvent.type(screen.getByLabelText("Supplier order number"), "X1");
    await userEvent.click(screen.getAllByRole("button", { name: "Mark as ordered" }).at(-1)!);
    expect(await screen.findByText(/cannot be marked placed/)).toBeInTheDocument();
  });

  it("lets a read-only user look but not act", async () => {
    who.role = "READ_ONLY";
    render(<Page />);
    const c = await card();
    expect(c.queryByRole("button", { name: "Mark as ordered" })).not.toBeInTheDocument();
    expect(screen.getByText(/Only staff, managers and owners can record/)).toBeInTheDocument();
  });

  it("switches between the queue's states", async () => {
    render(<Page />);
    await card();
    await userEvent.click(screen.getByRole("tab", { name: "Failed" }));
    await waitFor(() => expect(api.listSupplierOrders).toHaveBeenLastCalledWith("t", "FAILED"));
  });

  it("says plainly when there is nothing to order", async () => {
    api.listSupplierOrders.mockResolvedValue({ supplier_orders: [] });
    render(<Page />);
    expect(await screen.findByText("Nothing to order from suppliers right now.")).toBeInTheDocument();
  });
});
