import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { order } from "../../__tests__/fixtures";

const api = vi.hoisted(() => ({ listOrders: vi.fn() }));
vi.mock("@/lib/useAuth", () => ({ useAuth: () => ({ token: "t", user: { id: "u", role: "STAFF" }, loading: false }) }));
vi.mock("@/components/AppShell", () => ({ default: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock("@/lib/dropshippingApi", async () => ({ ...(await vi.importActual<object>("@/lib/dropshippingApi")), ...api }));
import Page from "@/app/dropshipping/orders/page";

beforeEach(() => api.listOrders.mockReset());

describe("Orders", () => {
  it("lists orders with plain-word status and only the shopper's name and city", async () => {
    api.listOrders.mockResolvedValue({ orders: [order(), order({ id: "o2", order_number: "DS-ERASED22", status: "DELIVERED", personal_data_erased: true, ship_to: null })] });
    render(<Page />);
    const row = (await screen.findByText("DS-ABCD2345")).closest("tr")!;
    expect(within(row).getByText("Paid, order from supplier")).toBeInTheDocument();
    expect(within(row).getByText("Pat Shopper, Austin")).toBeInTheDocument();
    expect(within(row).getByText("35.00 USD")).toBeInTheDocument();
    expect(row).not.toHaveTextContent("12 Main St");
    expect(row).not.toHaveTextContent("pat@example.com");
    expect(screen.getByText("DS-ERASED22").closest("tr")).toHaveTextContent("Erased");
    expect(screen.getByRole("link", { name: "DS-ABCD2345" })).toHaveAttribute("href", "/dropshipping/orders/o1");
  });

  it("filters by status and by order number", async () => {
    api.listOrders.mockResolvedValue({ orders: [order()] });
    render(<Page />);
    await screen.findByText("DS-ABCD2345");
    await userEvent.selectOptions(screen.getByLabelText("Filter by status"), "REFUND_REQUESTED");
    await waitFor(() => expect(api.listOrders).toHaveBeenLastCalledWith("t", { status: "REFUND_REQUESTED", q: undefined }));
    await userEvent.type(screen.getByLabelText("Order number"), "DS-ABCD");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(api.listOrders).toHaveBeenLastCalledWith("t", { status: "REFUND_REQUESTED", q: "DS-ABCD" }));
  });

  it("says plainly when no order matches", async () => {
    api.listOrders.mockResolvedValue({ orders: [] });
    render(<Page />);
    expect(await screen.findByText("No orders match.")).toBeInTheDocument();
  });

  it("shows an error with a retry", async () => {
    api.listOrders.mockRejectedValueOnce(new Error("down")).mockResolvedValue({ orders: [order()] });
    render(<Page />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Unable to load orders.");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("DS-ABCD2345")).toBeInTheDocument();
  });
});
