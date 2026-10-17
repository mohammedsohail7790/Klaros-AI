"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/Button";
import { StoreClosed } from "@/components/store/Footer";
import { usePricedCart, useStore } from "@/components/store/StoreProvider";
import { QtyInput } from "@/components/store/QtyInput";
import { estimate, formatMoney, fromCents } from "@/lib/storeCart";
import { lastOrder } from "@/lib/storeOrders";

/** The basket, with prices and stock read live. The totals here are an ESTIMATE from the published shipping rule; the real total is set by the store when you check out. */
export default function CartPage() {
  const { info, lines, ready, setQty, remove } = useStore();
  const { priced, loading, error } = usePricedCart(lines, ready);
  const [recent, setRecent] = useState<{ token: string; orderNumber: string } | null>(null);
  useEffect(() => setRecent(lastOrder()), []);

  if (!info) return <StoreClosed />;
  if (!ready || loading) return <p className="py-12 text-center text-muted" role="status">Loading your basket…</p>;
  if (lines.length === 0) {
    return (
      <div className="py-12 text-center">
        <h1 className="font-display text-3xl font-semibold tracking-tight text-foreground">Your basket is empty</h1>
        <p className="mt-3"><Link href="/store" className="underline">Browse products</Link></p>
        {recent && <p className="mt-2 text-sm text-muted"><Link href={`/store/orders/${recent.token}`} className="underline">View your last order ({recent.orderNumber})</Link></p>}
      </div>
    );
  }
  const est = estimate(info, priced);
  const money = (c: number) => formatMoney(fromCents(c), info.currency);

  return (
    <div className="space-y-6">
      <h1 className="font-display text-3xl font-semibold tracking-tight text-foreground">Your basket</h1>
      {error && <p role="alert" className="text-danger">We could not check today&apos;s prices: {error}</p>}
      <ul className="divide-y divide-border rounded-2xl border border-border bg-surface">
        {priced.map((l) => (
          <li key={l.sku} className="flex flex-wrap items-center gap-4 p-4" aria-label={l.name}>
            <div className="min-w-0 flex-1">
              <Link href={`/store/products/${l.slug}`} className="font-medium text-foreground">{l.name}</Link>
              <div className="text-sm text-muted">{l.sku}{l.price && ` · ${formatMoney(l.price, info.currency)} each`}</div>
              {l.gone && <p className="text-sm text-danger" role="alert">This item is no longer available. Please remove it.</p>}
              {!l.gone && !l.inStock && <p className="text-sm text-danger" role="alert">Out of stock. Please remove it to continue.</p>}
            </div>
            <div className="flex items-center gap-2 text-sm text-muted">Quantity <QtyInput value={l.quantity} onCommit={(n) => setQty(l.sku, n)} label={`Quantity of ${l.name}`} /></div>
            <div className="w-24 text-right">{l.lineCents != null ? money(l.lineCents) : "—"}</div>
            <Button variant="ghost" size="sm" onClick={() => remove(l.sku)} aria-label={`Remove ${l.name}`}>Remove</Button>
          </li>
        ))}
      </ul>
      <dl className="ml-auto grid max-w-xs grid-cols-2 gap-y-1 text-sm">
        <dt className="text-muted">Subtotal</dt><dd className="text-right">{money(est.subtotalCents)}</dd>
        <dt className="text-muted">Shipping (estimate)</dt><dd className="text-right">{money(est.shippingCents)}</dd>
        <dt className="font-medium">Estimated total</dt><dd className="text-right font-medium">{money(est.totalCents)}</dd>
      </dl>
      <p className="text-right text-xs text-muted">{info.sales_tax_notice} The final total is confirmed when you check out.</p>
      <div className="flex justify-end">
        {est.blocked ? <Button disabled>Checkout</Button> : <Link href="/store/checkout" className="klaros-btn-primary">Checkout</Link>}
      </div>
    </div>
  );
}
