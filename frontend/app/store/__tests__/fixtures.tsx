import { render } from "@testing-library/react";
import { StoreProvider } from "@/components/store/StoreProvider";
import type { StoreInfo, StoreOrder, StoreProduct } from "@/lib/storeApi";

export const INFO: StoreInfo = {
  open: true, currency: "USD", shipping: "Flat-rate shipping: $5.00. Free on orders of $50.00 or more.", free_shipping_over: "50.00", flat_shipping_fee: "5.00",
  sales_tax_notice: "Sales tax is not collected on these orders.", ships_to: "the continental United States",
};

export const product = (over: Partial<StoreProduct> = {}): StoreProduct => ({
  name: "Green Tea", slug: "green-tea", description: "Loose leaf green tea.", category: "Tea", image_url: null, non_perishable: true,
  items: [{ sku: "TEA-1", price: "10.00", currency: "USD", in_stock: true }], ...over,
});

export const order = (over: Partial<StoreOrder> = {}): StoreOrder => ({
  order_number: "DS-ABCD2345", status: "PENDING_PAYMENT", status_label: "Awaiting payment", currency: "USD", placed_at: "2026-10-16T10:00:00Z",
  subtotal: "30.00", shipping: "5.00", tax: "0.00", total: "35.00", can_pay: true,
  items: [{ name: "Green Tea", quantity: 3, unit_price: "10.00", line_total: "30.00" }], tracking: [], ...over,
});

export const CART_KEY = "klaros_store_cart";
export const saveCart = (lines: object[]) => localStorage.setItem(CART_KEY, JSON.stringify(lines));
export const teaLine = (quantity = 2) => ({ sku: "TEA-1", slug: "green-tea", name: "Green Tea", quantity });

export function renderInStore(ui: React.ReactElement, info: StoreInfo | null = INFO) {
  return render(<StoreProvider info={info}>{ui}</StoreProvider>);
}
