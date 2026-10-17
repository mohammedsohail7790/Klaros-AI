"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import AppShell from "@/components/AppShell";
import { ConfirmAction, ToneBadge } from "@/components/dropshipping/parts";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import {
  DsOrder, canCoordinate, canManage, cancelOrder, describeError, erasePersonalData, getOrder, markOrderPaid, requestRefund,
} from "@/lib/dropshippingApi";
import { money, ORDER_STATUS, shortDate, SUPPLIER_STATUS } from "@/lib/dropshippingLabels";
import { useAuth } from "@/lib/useAuth";

type Action = "paid" | "cancel" | "refund" | "erase" | null;

/** One order: what was bought, who it ships to, where the supplier work stands, and the actions its status allows. */
export default function DropshippingOrderPage() {
  const { id } = useParams<{ id: string }>();
  const { token, user, loading: authLoading } = useAuth();
  const [order, setOrder] = useState<DsOrder | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [action, setAction] = useState<Action>(null);

  const load = useCallback(async () => {
    if (!token || !id) return;
    setError(null);
    try {
      setOrder((await getOrder(token, id)).order);
    } catch (err) {
      setError(describeError(err, "Unable to load this order.").message);
    }
  }, [token, id]);

  useEffect(() => {
    load();
  }, [load]);

  // The service raises with a message and a list of reasons; the dialog shows both.
  const run = (fn: (t: string, text: string) => Promise<{ order: DsOrder }>) => async (text: string) => {
    try {
      setOrder((await fn(token!, text)).order);
    } catch (err) {
      throw describeError(err, "That did not work.");
    }
  };

  const manage = canManage(user?.role);
  const coordinate = canCoordinate(user?.role);
  const st = order?.status;
  const finished = st === "DELIVERED" || st === "CANCELLED" || st === "REFUND_REQUESTED";
  const canCancel = coordinate && (st === "PENDING_PAYMENT" || st === "PAID");
  const canRefund = coordinate && (st === "PAID" || st === "SUPPLIER_ORDER_PLACED" || st === "SHIPPED" || st === "DELIVERED");
  const canMarkPaid = manage && st === "PENDING_PAYMENT";
  const canErase = manage && finished && !!order && !order.personal_data_erased;

  return (
    <AppShell user={user}>
      <div className="mx-auto w-full max-w-5xl px-5 py-8 sm:px-8 sm:py-12">
        <Link href="/dropshipping/orders" className="mb-4 inline-flex items-center gap-1 text-sm text-muted hover:text-foreground"><ArrowLeft className="h-4 w-4" /> All orders</Link>
        {authLoading || (!order && !error) ? (
          <Skeleton />
        ) : error ? (
          <Alert variant="danger" action={<Button size="sm" variant="secondary" onClick={() => load()}>Try again</Button>}>{error}</Alert>
        ) : order ? (
          <div className="space-y-8">
            <header className="space-y-2">
              <div className="flex flex-wrap items-center gap-3">
                <h1 className="font-display text-[2rem] font-semibold tracking-[-0.03em] text-foreground">{order.order_number}</h1>
                <ToneBadge tone={ORDER_STATUS[order.status].tone}>{ORDER_STATUS[order.status].label}</ToneBadge>
              </div>
              <p className="text-muted">{ORDER_STATUS[order.status].hint} Placed {shortDate(order.placed_at)}.</p>
              {order.status_note && <Alert variant="warning">{order.status_note}</Alert>}
            </header>

            <div className="flex flex-wrap gap-2" aria-label="Order actions">
              {canMarkPaid && <Button onClick={() => setAction("paid")}>Record a payment</Button>}
              {canCancel && <Button variant="secondary" onClick={() => setAction("cancel")}>Cancel the order</Button>}
              {canRefund && <Button variant="secondary" onClick={() => setAction("refund")}>Flag a refund</Button>}
              {canErase && <Button variant="secondary" onClick={() => setAction("erase")}>Erase personal data</Button>}
            </div>

            <section aria-label="Items">
              <h2 className="mb-2 font-display text-[1.5rem] font-semibold tracking-[-0.02em] text-foreground">Items</h2>
              <div className="klaros-table-wrap"><table className="klaros-table">
                <thead><tr><th className="px-5 py-4">Item</th><th className="px-5 py-4">Quantity</th><th className="px-5 py-4">Price</th><th className="px-5 py-4">Total</th></tr></thead>
                <tbody>
                  {order.lines.map((l) => (
                    <tr key={l.sku_id} className="border-t border-border"><td className="px-5 py-4">{l.name}{l.sku_code && <span className="ml-2 text-muted">{l.sku_code}</span>}</td><td className="px-5 py-4">{l.quantity}</td><td className="px-5 py-4">{money(l.unit_price, order.currency)}</td><td className="px-5 py-4">{money(l.line_total, order.currency)}</td></tr>
                  ))}
                </tbody>
              </table></div>
              <dl className="mt-3 ml-auto grid w-full max-w-xs grid-cols-2 gap-y-1 text-sm">
                <dt className="text-muted">Subtotal</dt><dd className="text-right">{money(order.subtotal, order.currency)}</dd>
                <dt className="text-muted">Shipping</dt><dd className="text-right">{money(order.shipping, order.currency)}</dd>
                <dt className="text-muted">Sales tax</dt><dd className="text-right">{money(order.tax, order.currency)}</dd>
                <dt className="font-medium">Total</dt><dd className="text-right font-medium">{money(order.total, order.currency)}</dd>
              </dl>
            </section>

            <section aria-label="Shopper and payment" className="grid gap-6 sm:grid-cols-2">
              <div>
                <h2 className="mb-2 font-display text-[1.25rem] font-semibold text-foreground">Ships to</h2>
                {order.personal_data_erased ? <p className="text-muted">Personal data was erased.</p> : order.ship_to ? (
                  <address className="not-italic text-[15px] leading-relaxed">
                    {order.ship_to.name}<br />{order.ship_to.line1}<br />{order.ship_to.line2 && <>{order.ship_to.line2}<br /></>}{order.ship_to.city}, {order.ship_to.state} {order.ship_to.postal_code}
                    <div className="mt-2 text-muted">{order.contact_email}{order.contact_phone && ` · ${order.contact_phone}`}</div>
                  </address>
                ) : <p className="text-muted">—</p>}
              </div>
              <div>
                <h2 className="mb-2 font-display text-[1.25rem] font-semibold text-foreground">Payment</h2>
                {order.paid_at ? (
                  <p className="text-[15px]">Paid {shortDate(order.paid_at)} by {order.payment_source === "STRIPE" ? "card (Stripe)" : "a manual record"}.<br /><span className="text-muted">Reference: {order.payment_reference ?? "—"}</span></p>
                ) : <p className="text-muted">Not paid yet.</p>}
              </div>
            </section>

            <section aria-label="Supplier orders">
              <div className="mb-2 flex items-center justify-between"><h2 className="font-display text-[1.5rem] font-semibold tracking-[-0.02em] text-foreground">With suppliers</h2><Link href="/dropshipping/fulfilment" className="klaros-btn-secondary">Open fulfilment</Link></div>
              {order.supplier_orders.length === 0 ? <p className="text-muted">Nothing has been sent to a supplier yet. This happens once the order is paid.</p> : (
                <ul className="space-y-3">
                  {order.supplier_orders.map((so) => (
                    <li key={so.id} className="rounded-2xl border border-border p-4">
                      <div className="flex flex-wrap items-center gap-3"><span className="font-medium">{so.supplier ?? "Supplier"}</span><ToneBadge tone={SUPPLIER_STATUS[so.status].tone}>{SUPPLIER_STATUS[so.status].label}</ToneBadge>{so.supplier_reference && <span className="text-muted">Their order {so.supplier_reference}</span>}</div>
                      {so.note && <p className="mt-1 text-sm text-muted">{so.note}</p>}
                      {so.shipments.map((sh) => <p key={sh.id} className="mt-1 text-sm">{sh.carrier ?? "Carrier"} {sh.tracking_url ? <a className="underline" href={sh.tracking_url} target="_blank" rel="noreferrer">{sh.tracking_number}</a> : sh.tracking_number} · {sh.status.toLowerCase()}</p>)}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        ) : null}
      </div>

      {action === "paid" && <ConfirmAction title="Record a payment" body="Use this for a payment that did not go through the store, such as a bank transfer. Card payments are recorded automatically." fieldLabel="Payment reference" fieldHint="For example the transfer or receipt number" confirmLabel="Record payment" onConfirm={run((t, text) => markOrderPaid(t, id, text))} onClose={() => setAction(null)} />}
      {action === "cancel" && <ConfirmAction danger title="Cancel this order?" body={order?.status === "PAID" ? "It is paid, so cancelling flags a refund. Klaros does not return money: refund it in Stripe." : "It has not been paid, so nothing needs refunding."} fieldLabel="Reason" confirmLabel="Cancel the order" onConfirm={run((t, text) => cancelOrder(t, id, text))} onClose={() => setAction(null)} />}
      {action === "refund" && <ConfirmAction danger title="Flag a refund?" body="This marks the order as needing money returned (damaged, lost or returned). Klaros does not return money: refund it in Stripe." fieldLabel="Reason" confirmLabel="Flag the refund" onConfirm={run((t, text) => requestRefund(t, id, text))} onClose={() => setAction(null)} />}
      {action === "erase" && <ConfirmAction danger title="Erase personal data?" body="This removes the address, email and phone from this order and ends the shopper's order link. Totals, items and tracking stay. It cannot be undone." confirmLabel="Erase" onConfirm={run((t) => erasePersonalData(t, id))} onClose={() => setAction(null)} />}
    </AppShell>
  );
}
