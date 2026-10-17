import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { storeView } from "../../__tests__/fixtures";

const api = vi.hoisted(() => ({ getStoreSettings: vi.fn(), putStoreSettings: vi.fn() }));
const who = vi.hoisted(() => ({ role: "OWNER" }));
vi.mock("@/lib/useAuth", () => ({ useAuth: () => ({ token: "t", user: { id: "u", role: who.role }, loading: false }) }));
vi.mock("@/components/AppShell", () => ({ default: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock("@/lib/dropshippingApi", async () => ({ ...(await vi.importActual<object>("@/lib/dropshippingApi")), ...api }));
import { ApiError } from "@/lib/api";
import Page from "@/app/dropshipping/store/page";

beforeEach(() => {
  who.role = "OWNER";
  Object.values(api).forEach((f) => f.mockReset());
  api.getStoreSettings.mockResolvedValue(storeView({ blockers: ["Set a shipping rule (a flat fee, which may be 0).", "Acknowledge that sales tax is not collected on orders."] }));
});

describe("Store settings", () => {
  it("lists what blocks the store from opening", async () => {
    render(<Page />);
    expect(await screen.findByText("The store is closed to shoppers.")).toBeInTheDocument();
    expect(screen.getByText(/Set a shipping rule/)).toBeInTheDocument();
    expect(screen.getByText(/Acknowledge that sales tax/)).toBeInTheDocument();
  });

  it("saves the shipping rule exactly as typed and shows the server's answer", async () => {
    api.putStoreSettings.mockResolvedValue(storeView({}, { shipping_flat_fee: "5.00", free_shipping_over: "50.00" }));
    render(<Page />);
    await userEvent.type(await screen.findByLabelText(/Flat shipping fee/), "5.00");
    await userEvent.type(screen.getByLabelText(/Free shipping from/), "50.00");
    await userEvent.click(screen.getByRole("button", { name: "Save shipping" }));
    await waitFor(() => expect(api.putStoreSettings).toHaveBeenCalledWith("t", { shipping_flat_fee: "5.00", free_shipping_over: "50.00" }));
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
  });

  it("will not save a shipping rule until a fee is typed", async () => {
    render(<Page />);
    expect(await screen.findByRole("button", { name: "Save shipping" })).toBeDisabled();
  });

  it("records the sales-tax acknowledgement", async () => {
    api.putStoreSettings.mockResolvedValue(storeView({}, { sales_tax_acknowledged: true }));
    render(<Page />);
    await userEvent.click(await screen.findByRole("checkbox"));
    await waitFor(() => expect(api.putStoreSettings).toHaveBeenCalledWith("t", { sales_tax_acknowledged: true }));
  });

  it("shows the server's reasons when opening the store is refused", async () => {
    api.putStoreSettings.mockRejectedValue(new ApiError(422, "The request was refused.", undefined, { message: "The request was refused.", problems: ["The store cannot be switched on yet:", "Stripe is not connected (no connection and no platform key)."] }));
    render(<Page />);
    await userEvent.click(await screen.findByRole("button", { name: "Open the store" }));
    expect(await screen.findByText(/Stripe is not connected/)).toBeInTheDocument();
  });

  it("offers to close an open store", async () => {
    api.getStoreSettings.mockResolvedValue(storeView({ open_to_shoppers: true }, { open: true, shipping_flat_fee: "5.00", sales_tax_acknowledged: true }));
    api.putStoreSettings.mockResolvedValue(storeView({}, { open: false, shipping_flat_fee: "5.00", sales_tax_acknowledged: true }));
    render(<Page />);
    expect(await screen.findByText("The store is open to shoppers.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Close the store" }));
    await waitFor(() => expect(api.putStoreSettings).toHaveBeenCalledWith("t", { open: false }));
  });

  it("is read-only for anyone who is not a manager or owner", async () => {
    who.role = "STAFF";
    render(<Page />);
    expect(await screen.findByText(/Only an owner or manager can change them/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save shipping" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open the store" })).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox")).toBeDisabled();
  });
});
