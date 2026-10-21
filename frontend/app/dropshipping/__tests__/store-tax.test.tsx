import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { storeView } from "./fixtures";

const api = vi.hoisted(() => ({ getStoreSettings: vi.fn(), putStoreSettings: vi.fn(), listSuppliers: vi.fn() }));
const auth = vi.hoisted(() => ({ role: "OWNER" }));
vi.mock("@/lib/useAuth", () => ({ useAuth: () => ({ token: "t", user: { id: "u", role: auth.role }, loading: false }) }));
vi.mock("@/components/AppShell", () => ({ default: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock("@/lib/dropshippingApi", async () => ({ ...(await vi.importActual<object>("@/lib/dropshippingApi")), ...api }));
import Page from "@/app/dropshipping/store/page";

beforeEach(() => {
  auth.role = "OWNER";
  Object.values(api).forEach((f) => f.mockReset());
  api.listSuppliers.mockResolvedValue({ suppliers: [] });
  api.getStoreSettings.mockResolvedValue(storeView({}, { shipping_flat_fee: "5.00" }));
});

const taxSection = async () => within(await screen.findByRole("region", { name: "Sales tax" }));

describe("store settings: sales tax", () => {
  it("offers Stripe Tax or no tax, and asks for the acknowledgement only when no tax is collected", async () => {
    render(<Page />);
    const tax = await taxSection();
    expect(tax.getByRole("radio", { name: /Do not collect sales tax/ })).toBeChecked();
    expect(tax.getByRole("radio", { name: /Collect sales tax with Stripe Tax/ })).not.toBeChecked();
    expect(tax.getByRole("checkbox", { name: /sales tax is not collected/ })).toBeInTheDocument();
  });

  it("chooses Stripe Tax and explains what Stripe needs, the tax code and the candy caveat", async () => {
    api.putStoreSettings.mockResolvedValue(storeView({}, { shipping_flat_fee: "5.00", sales_tax_mode: "STRIPE_TAX", tax_code: "txcd_40040000" }));
    render(<Page />);
    await userEvent.click((await taxSection()).getByRole("radio", { name: /Collect sales tax with Stripe Tax/ }));
    expect(api.putStoreSettings).toHaveBeenCalledWith("t", { sales_tax_mode: "STRIPE_TAX" });
    const tax = await taxSection();
    await waitFor(() => expect(tax.getByRole("radio", { name: /Collect sales tax with Stripe Tax/ })).toBeChecked());
    expect(tax.getByText(/active tax registration/)).toBeInTheDocument();
    expect(tax.getByText("txcd_40040000")).toBeInTheDocument();
    expect(tax.getByText(/Candy, soft drinks and dietary supplements/)).toBeInTheDocument();
    expect(tax.queryByRole("checkbox")).not.toBeInTheDocument();       // no acknowledgement is needed when tax is collected
  });

  it("shows why Stripe Tax was refused and keeps the old choice", async () => {
    api.putStoreSettings.mockRejectedValue(new ApiError(422, "The request was refused.", undefined, { problems: ["Stripe Tax is not ready yet:", "Your Stripe account has no active tax registration."] }));
    render(<Page />);
    await userEvent.click((await taxSection()).getByRole("radio", { name: /Collect sales tax with Stripe Tax/ }));
    expect(await screen.findByText(/no active tax registration/)).toBeInTheDocument();
    expect((await taxSection()).getByRole("radio", { name: /Do not collect sales tax/ })).toBeChecked();
  });

  it("lets only a manager change it", async () => {
    auth.role = "STAFF";
    render(<Page />);
    const tax = await taxSection();
    expect(tax.getByRole("radio", { name: /Collect sales tax with Stripe Tax/ })).toBeDisabled();
  });
});
