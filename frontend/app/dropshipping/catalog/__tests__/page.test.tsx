import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { product } from "../../__tests__/fixtures";

const api = vi.hoisted(() => ({
  listProducts: vi.fn(), publishProduct: vi.fn(), archiveProduct: vi.fn(), setAvailability: vi.fn(), createProduct: vi.fn(),
  listSuppliers: vi.fn(), createSupplier: vi.fn(), previewImport: vi.fn(), commitImport: vi.fn(),
}));
const who = vi.hoisted(() => ({ role: "MANAGER" }));
vi.mock("@/lib/useAuth", () => ({ useAuth: () => ({ token: "t", user: { id: "u", role: who.role }, loading: false }) }));
vi.mock("@/components/AppShell", () => ({ default: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock("@/lib/dropshippingApi", async () => ({ ...(await vi.importActual<object>("@/lib/dropshippingApi")), ...api }));
import { ApiError } from "@/lib/api";
import Page from "@/app/dropshipping/catalog/page";

const report = (over: object = {}) => ({
  ok: true, committed: false, token: "tok1", file_problems: [], rows: [], summary: { rows: 2, skus_created: 2, skus_updated: 0, skus_unchanged: 0, products_created: 2, products_updated: 0, rejected: 0 }, ...over,
});

beforeEach(() => {
  who.role = "MANAGER";
  Object.values(api).forEach((f) => f.mockReset());
  api.listProducts.mockResolvedValue({ products: [product()] });
  api.listSuppliers.mockResolvedValue({ suppliers: [{ id: "su1", name: "Acme Supply", integration_type: "MANUAL", status: "ACTIVE" }] });
});

const item = async (name = "Green Tea") => within(await screen.findByRole("listitem", { name }));

describe("Catalog", () => {
  it("shows price, cost, margin and stock as stored", async () => {
    render(<Page />);
    const p = await item();
    expect(p.getByText("Draft")).toBeInTheDocument();
    expect(p.getByText("10.00 USD")).toBeInTheDocument();
    expect(p.getByText("6.00")).toBeInTheDocument();
    expect(p.getByText("4.00")).toBeInTheDocument();
    expect(p.getByLabelText("Stock for TEA-1")).toHaveValue("AVAILABLE");
  });

  it("changes stock", async () => {
    api.setAvailability.mockResolvedValue({ sku: {} });
    render(<Page />);
    await userEvent.selectOptions((await item()).getByLabelText("Stock for TEA-1"), "OUT_OF_STOCK");
    await waitFor(() => expect(api.setAvailability).toHaveBeenCalledWith("t", "k1", "OUT_OF_STOCK"));
  });

  it("shows why a draft cannot be published, and the server's refusal when it is tried", async () => {
    api.listProducts.mockResolvedValue({ products: [product({ publish_problems: ["Mark the product non-perishable"] })] });
    api.publishProduct.mockRejectedValue(new ApiError(422, "The request was refused.", undefined, { message: "The request was refused.", problems: ["Mark the product non-perishable"] }));
    render(<Page />);
    expect(await screen.findByText(/Not ready to publish: Mark the product non-perishable/)).toBeInTheDocument();
    await userEvent.click((await item()).getByRole("button", { name: "Publish" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Mark the product non-perishable");
  });

  it("publishes a ready draft and reloads", async () => {
    api.publishProduct.mockResolvedValue({ product: product({ status: "ACTIVE" }) });
    render(<Page />);
    await userEvent.click((await item()).getByRole("button", { name: "Publish" }));
    await waitFor(() => expect(api.publishProduct).toHaveBeenCalledWith("t", "p1"));
    await waitFor(() => expect(api.listProducts).toHaveBeenCalledTimes(2));
  });

  it("asks before archiving", async () => {
    api.archiveProduct.mockResolvedValue({ product: product({ status: "ARCHIVED" }) });
    render(<Page />);
    await userEvent.click((await item()).getByRole("button", { name: "Archive" }));
    expect(screen.getByText(/Existing orders are not affected/)).toBeInTheDocument();
    expect(api.archiveProduct).not.toHaveBeenCalled();
    await userEvent.click(screen.getAllByRole("button", { name: "Archive" }).at(-1)!);
    await waitFor(() => expect(api.archiveProduct).toHaveBeenCalledWith("t", "p1"));
  });

  it("gives staff a view with no way to change anything", async () => {
    who.role = "STAFF";
    render(<Page />);
    const p = await item();
    expect(p.queryByRole("button")).not.toBeInTheDocument();
    expect(p.queryByLabelText("Stock for TEA-1")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add a product" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Import CSV" })).not.toBeInTheDocument();
  });

  it("adds a product as a draft once the required fields are filled", async () => {
    api.createProduct.mockResolvedValue({ product: product() });
    render(<Page />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a product" }));
    const save = await screen.findByRole("button", { name: "Save as draft" });
    expect(save).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Name"), "Brown Rice");
    await userEvent.type(screen.getByLabelText("Item code (yours)"), "RICE-1");
    await userEvent.type(screen.getByLabelText("Price to shoppers (USD)"), "8.50");
    await userEvent.selectOptions(screen.getByLabelText("Supplier"), "Acme Supply");
    await userEvent.click(save);
    await waitFor(() => expect(api.createProduct).toHaveBeenCalledWith("t", expect.objectContaining({
      name: "Brown Rice", is_non_perishable: true, skus: [expect.objectContaining({ sku_code: "RICE-1", price: "8.50", supplier: "Acme Supply", availability: "AVAILABLE" })],
    })));
  });

  describe("CSV import", () => {
    const open = async () => {
      render(<Page />);
      await userEvent.click(await screen.findByRole("button", { name: "Import CSV" }));
      await userEvent.type(screen.getByLabelText("CSV text"), "sku_code,product_name");
    };

    it("will not import until the whole file has been checked and passes", async () => {
      api.previewImport.mockResolvedValue(report());
      await open();
      expect(screen.getByRole("button", { name: "Import" })).toBeDisabled();
      await userEvent.click(screen.getByRole("button", { name: "Check the file" }));
      expect(await screen.findByText(/Ready: 2 rows/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Import" })).toBeEnabled();
    });

    it("lists every problem and keeps Import disabled when any row is rejected", async () => {
      api.previewImport.mockResolvedValue(report({ ok: false, token: null, summary: { rows: 2, skus_created: 0, skus_updated: 0, skus_unchanged: 0, products_created: 0, products_updated: 0, rejected: 1 }, rows: [{ row: 3, sku_code: "BAD-1", action: "REJECTED", problems: ["price must be greater than 0"] }] }));
      await open();
      await userEvent.click(screen.getByRole("button", { name: "Check the file" }));
      expect((await screen.findByText(/Row 3 \(BAD-1\):/)).closest("li")).toHaveTextContent("price must be greater than 0");
      expect(screen.getByText(/Nothing was saved/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Import" })).toBeDisabled();
    });

    it("commits exactly the previewed file with its token, then says what was added", async () => {
      api.previewImport.mockResolvedValue(report());
      api.commitImport.mockResolvedValue(report({ committed: true }));
      await open();
      await userEvent.click(screen.getByRole("button", { name: "Check the file" }));
      await userEvent.click(await screen.findByRole("button", { name: "Import" }));
      await waitFor(() => expect(api.commitImport).toHaveBeenCalledWith("t", "sku_code,product_name", "tok1"));
      expect(await screen.findByText(/Imported\. 2 new products/)).toBeInTheDocument();
      expect(screen.getByText(/drafts until you publish/)).toBeInTheDocument();
    });

    it("requires a new check after the text is edited", async () => {
      api.previewImport.mockResolvedValue(report());
      await open();
      await userEvent.click(screen.getByRole("button", { name: "Check the file" }));
      await screen.findByText(/Ready: 2 rows/);
      await userEvent.type(screen.getByLabelText("CSV text"), ",extra");
      expect(screen.getByRole("button", { name: "Import" })).toBeDisabled();
    });
  });

  it("says plainly when the catalog is empty", async () => {
    api.listProducts.mockResolvedValue({ products: [] });
    render(<Page />);
    expect(await screen.findByText("No products yet.")).toBeInTheDocument();
  });
});
