import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import ShippingRule from "@/components/dropshipping/ShippingRule";
import type { StoreSettings } from "@/lib/dropshippingApi";

const settings = (over: Partial<StoreSettings> = {}): StoreSettings => ({ open: false, shipping_flat_fee: null, free_shipping_over: null, sales_tax_acknowledged: false, ...over });
const setup = (s: StoreSettings, manage = true) => {
  const onSave = vi.fn();
  render(<ShippingRule settings={s} manage={manage} busy={false} onSave={onSave} />);
  return onSave;
};

describe("shipping rule (staff)", () => {
  it("saves a flat fee and clears any tiers", async () => {
    const onSave = setup(settings());
    await userEvent.type(screen.getByLabelText(/Flat shipping fee/), "5.00");
    await userEvent.type(screen.getByLabelText(/Free shipping from/), "50.00");
    await userEvent.click(screen.getByRole("button", { name: "Save shipping" }));
    expect(onSave).toHaveBeenCalledWith({ shipping_tiers: null, shipping_flat_fee: "5.00", free_shipping_over: "50.00" });
  });

  it("builds weight tiers, the last one without a limit", async () => {
    const onSave = setup(settings());
    await userEvent.click(screen.getByRole("radio", { name: /By the weight of the order/ }));
    const tiers = () => within(screen.getByRole("list", { name: "Shipping tiers" }));
    await userEvent.type(tiers().getByLabelText("Up to (lb)"), "1");
    await userEvent.type(tiers().getByLabelText("Fee ($)"), "6.95");
    await userEvent.click(screen.getByRole("button", { name: /Add a tier/ }));
    await userEvent.type(tiers().getByLabelText("Fee ($), tier 2"), "10.95");           // no limit: every heavier order
    await userEvent.click(screen.getByRole("button", { name: "Save shipping" }));
    expect(onSave).toHaveBeenCalledWith({ shipping_tiers: [{ max_lb: "1", fee: "6.95" }, { max_lb: null, fee: "10.95" }], free_shipping_over: null });
  });

  it("cannot be saved while a tier has no fee, and a tier can be removed", async () => {
    setup(settings({ shipping_tiers: [{ max_lb: "1", fee: "6.95" }, { max_lb: null, fee: "" }] }));
    expect(screen.getByRole("button", { name: "Save shipping" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Remove tier 2" }));
    expect(screen.getByRole("button", { name: "Save shipping" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: /Remove tier/ })).not.toBeInTheDocument();      // the only tier left cannot be removed
  });

  it("opens in tier mode when the store already ships by weight, and is read-only without permission", () => {
    setup(settings({ shipping_tiers: [{ max_lb: "1", fee: "6.95" }, { max_lb: null, fee: "53.95" }] }), false);
    expect(screen.getByRole("radio", { name: /By the weight of the order/ })).toBeChecked();
    expect(screen.getAllByLabelText(/^Fee/)[0]).toHaveValue("6.95");
    expect(screen.getAllByLabelText(/^Fee/)[0]).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Save shipping" })).not.toBeInTheDocument();
  });
});
