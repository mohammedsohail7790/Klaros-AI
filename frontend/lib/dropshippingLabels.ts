import type { Availability, OrderStatus, SupplierOrderStatus } from "@/lib/dropshippingApi";

export type Tone = "neutral" | "warning" | "success" | "danger" | "info";

/** What staff read for each order status: plain words, and what (if anything) is waiting on a person. */
export const ORDER_STATUS: Record<OrderStatus, { label: string; tone: Tone; hint: string }> = {
  PENDING_PAYMENT: { label: "Waiting for payment", tone: "warning", hint: "The shopper has not paid yet." },
  PAID: { label: "Paid, order from supplier", tone: "info", hint: "Paid. Place the order with the supplier." },
  SUPPLIER_ORDER_PLACED: { label: "With the supplier", tone: "info", hint: "Placed with the supplier. Add tracking when it ships." },
  SHIPPED: { label: "Shipped", tone: "info", hint: "On its way to the shopper." },
  DELIVERED: { label: "Delivered", tone: "success", hint: "Delivered." },
  CANCELLED: { label: "Cancelled", tone: "neutral", hint: "Cancelled before payment." },
  REFUND_REQUESTED: { label: "Refund needed", tone: "danger", hint: "Money must be returned in Stripe. Klaros does not move money." },
};

export const SUPPLIER_STATUS: Record<SupplierOrderStatus, { label: string; tone: Tone }> = {
  TO_PLACE: { label: "To order", tone: "warning" },
  PLACED: { label: "Placed", tone: "info" },
  CONFIRMED: { label: "Confirmed", tone: "success" },
  FAILED: { label: "Failed", tone: "danger" },
  CANCELLED: { label: "Cancelled", tone: "neutral" },
};

export const AVAILABILITY: Record<Availability, string> = { AVAILABLE: "In stock", OUT_OF_STOCK: "Out of stock", UNKNOWN: "Unknown" };

export const STATUS_FILTERS: { value: "" | OrderStatus; label: string }[] = [
  { value: "", label: "All orders" },
  { value: "PENDING_PAYMENT", label: "Waiting for payment" },
  { value: "PAID", label: "Paid, to order" },
  { value: "SUPPLIER_ORDER_PLACED", label: "With the supplier" },
  { value: "SHIPPED", label: "Shipped" },
  { value: "DELIVERED", label: "Delivered" },
  { value: "REFUND_REQUESTED", label: "Refund needed" },
  { value: "CANCELLED", label: "Cancelled" },
];

export function money(amount: string | null | undefined, currency = "USD"): string {
  return amount == null ? "—" : `${amount} ${currency}`;
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

/** The address as a block of text for the supplier's order form. */
export function addressBlock(a: { name: string; line1: string; line2?: string | null; city: string; state: string; postal_code: string } | null | undefined): string {
  if (!a) return "";
  return [a.name, a.line1, a.line2, `${a.city}, ${a.state} ${a.postal_code}`].filter(Boolean).join("\n");
}
