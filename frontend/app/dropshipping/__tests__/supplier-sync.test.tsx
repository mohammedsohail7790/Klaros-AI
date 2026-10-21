import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SyncableSupplier } from "@/lib/dropshippingApi";

const api = vi.hoisted(() => ({ listSuppliers: vi.fn(), saveSyncSettings: vi.fn(), runSupplierSync: vi.fn() }));
vi.mock("@/lib/dropshippingApi", async () => ({ ...(await vi.importActual<object>("@/lib/dropshippingApi")), ...api }));
import SupplierSync from "@/components/dropshipping/SupplierSync";

const supplier = (over: Partial<SyncableSupplier> = {}): SyncableSupplier => ({
  id: "s1", name: "US Direct", integration_type: "MANUAL", status: "ACTIVE",
  sync: { feed_url: null, markup_percent: null, stock_buffer: 0, max_price_change_percent: "30.00", sync_enabled: false, last_sync_at: null, last_sync_summary: null }, ...over,
});

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  api.listSuppliers.mockResolvedValue({ suppliers: [supplier()] });
});

describe("Supplier stock and price sync", () => {
  it("says so when no sync has ever run", async () => {
    render(<SupplierSync token="t" manage />);
    const card = await screen.findByRole("region", { name: "US Direct sync" });
    expect(within(card).getByText("Never run.")).toBeInTheDocument();
  });

  it("saves the feed link, the markup and the daily switch", async () => {
    api.saveSyncSettings.mockResolvedValue(supplier({ sync: { ...supplier().sync!, feed_url: "https://feeds.example.com/a.csv", markup_percent: "50.00", sync_enabled: true } }));
    render(<SupplierSync token="t" manage />);
    const card = await screen.findByRole("region", { name: "US Direct sync" });
    fireEvent.change(within(card).getByLabelText(/Feed link/), { target: { value: "https://feeds.example.com/a.csv" } });
    fireEvent.change(within(card).getByLabelText(/Markup on cost/), { target: { value: "50" } });
    fireEvent.click(within(card).getByLabelText(/Sync this feed automatically/));
    fireEvent.click(within(card).getByRole("button", { name: "Save sync settings" }));
    await waitFor(() => expect(api.saveSyncSettings).toHaveBeenCalledWith("t", "s1", { feed_url: "https://feeds.example.com/a.csv", markup_percent: "50", stock_buffer: 0, max_price_change_percent: "30.00", sync_enabled: true }));
    expect(await within(card).findByText("Saved.")).toBeInTheDocument();
  });

  it("previews first, and only applies after a preview", async () => {
    api.runSupplierSync.mockResolvedValueOnce({ dry_run: true, matched: 4, unchanged: 1, to_out_of_stock: 2, prices_changed: 1 }).mockResolvedValueOnce({ applied: true, matched: 4, to_out_of_stock: 2 });
    render(<SupplierSync token="t" manage />);
    const card = await screen.findByRole("region", { name: "US Direct sync" });
    const apply = within(card).getByRole("button", { name: "Apply this run" });
    expect(apply).toBeDisabled();                                         // nothing to apply before a preview
    fireEvent.change(within(card).getByLabelText("Feed CSV"), { target: { value: "sku,qty\nA,0\n" } });
    fireEvent.click(within(card).getByRole("button", { name: /Preview/ }));
    const result = await within(card).findByLabelText("Preview result");
    expect(result).toHaveTextContent("4 items matched the feed (1 already up to date)");
    expect(result).toHaveTextContent("2 now sold out");
    expect(api.runSupplierSync).toHaveBeenLastCalledWith("t", "s1", { dry_run: true, force: false, csv_text: "sku,qty\nA,0\n" });
    fireEvent.click(apply);
    expect(await within(card).findByLabelText("Applied result")).toHaveTextContent("Applied.");
    expect(api.runSupplierSync).toHaveBeenLastCalledWith("t", "s1", { dry_run: false, force: false, csv_text: "sku,qty\nA,0\n" });
  });

  it("shows held changes and the server's refusals", async () => {
    api.runSupplierSync.mockResolvedValueOnce({ dry_run: true, matched: 12, availability_held: true, held_prices: [{ sku_code: "TEA-1", from: "9.00", to: "18.00", change_percent: "100.0" }], held_price_count: 1 });
    render(<SupplierSync token="t" manage />);
    const card = await screen.findByRole("region", { name: "US Direct sync" });
    fireEvent.click(within(card).getByRole("button", { name: /Preview/ }));
    const result = await within(card).findByLabelText("Preview result");
    expect(result).toHaveTextContent("Stock changes were held");
    expect(result).toHaveTextContent("TEA-1: $9.00 to $18.00 (100.0%)");
    api.runSupplierSync.mockRejectedValueOnce(Object.assign(new Error("refused"), { status: 422, detail: { message: "The request was refused.", problems: ["The feed is empty."] } }));
    fireEvent.click(within(card).getByRole("button", { name: /Preview/ }));
    await waitFor(() => expect(within(card).getAllByRole("alert").length).toBeGreaterThan(0));
  });

  it("is read-only without permission and remembers the last run", async () => {
    api.listSuppliers.mockResolvedValue({ suppliers: [supplier({ sync: { ...supplier().sync!, last_sync_at: "2026-10-21T08:00:00Z", last_sync_summary: { error: "The feed answered with status 503." } } })] });
    render(<SupplierSync token="t" manage={false} />);
    const card = await screen.findByRole("region", { name: "US Direct sync" });
    expect(card).toHaveTextContent("failed: The feed answered with status 503.");
    expect(within(card).queryByRole("button", { name: "Save sync settings" })).not.toBeInTheDocument();
    expect(within(card).getByLabelText(/Feed link/)).toBeDisabled();
  });
});
