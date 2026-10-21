"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ChevronLeft, Lock, Trash2 } from "lucide-react";
import { StoreClosed } from "@/components/store/Footer";
import { ProductVisual } from "@/components/store/LabelTile";
import { usePricedCart, useStore } from "@/components/store/StoreProvider";
import { buttonClass } from "@/components/store/styles";
import { Button, Card, FreeShippingMeter, Skeleton, Stepper } from "@/components/store/ui";
import { estimate, formatMoney, fromCents, toCents } from "@/lib/storeCart";
import { lastOrder } from "@/lib/storeOrders";

/** The basket, with prices and stock read live. The totals here are an ESTIMATE from the published shipping rule; the real total is set by the store when you check out. */
export default function CartPage() {
  const { info, lines, ready, setQty, remove } = useStore();
  const { priced, loading, error } = usePricedCart(lines, ready);
  const [recent, setRecent] = useState<{ token: string; orderNumber: string } | null>(null);
  useEffect(() => setRecent(lastOrder()), []);

  if (!info) return <StoreClosed />;
  if (!ready || loading) {
    return (
      <div role="status" aria-label="Loading your basket" className="grid gap-10 lg:grid-cols-[1fr_380px]">
        <div className="space-y-4"><Skeleton className="h-14 w-72" /><Skeleton className="h-28" /><Skeleton className="h-28" /></div>
        <Skeleton className="h-80" />
        <span className="sr-only">Loading your basket…</span>
      </div>
    );
  }
  if (lines.length === 0) {
    return (
      <div className="mx-auto max-w-lg py-20 text-center">
        <h1 className="text-[44px] sm:text-[56px]">Your basket is empty</h1>
        <p className="mt-4 text-[19px] text-muted">Add a few things and they will wait for you here.</p>
        <p className="mt-9"><Link href="/store" className={buttonClass("primary", "lg")}>Browse products</Link></p>
        {recent && <p className="mt-6 text-[15px] text-muted"><Link href={`/store/orders/${recent.token}`} className="text-[rgb(var(--color-brand))] underline underline-offset-4">View your last order ({recent.orderNumber})</Link></p>}
      </div>
    );
  }
  const est = estimate(info, priced);
  const money = (c: number) => formatMoney(fromCents(c), info.currency);
  const threshold = toCents(info.free_shipping_over);

  return (
    <div>
      <Link href="/store" className="mb-6 inline-flex items-center gap-1 text-[15px] text-[rgb(var(--color-brand))] hover:underline"><ChevronLeft className="h-4 w-4" aria-hidden /> Keep shopping</Link>
      <h1 className="mb-10 text-[44px] sm:text-[56px]">Your basket</h1>
      {error && <p role="alert" className="mb-4 text-red-800">We could not check today&apos;s prices: {error}</p>}
      <div className="grid items-start gap-10 lg:grid-cols-[1fr_380px] lg:gap-16">
        <ul className="divide-y divide-[rgb(var(--color-border))] border-y border-[rgb(var(--color-border))]">
          {priced.map((l) => (
            <li key={l.sku} aria-label={l.name} className="grid grid-cols-[84px_1fr] gap-x-5 gap-y-4 py-6 sm:grid-cols-[104px_1fr_auto] sm:items-center sm:gap-x-7">
              <ProductVisual name={l.name} slug={l.slug} category={l.category} imageUrl={l.imageUrl} size="thumb" />
              <div className="min-w-0">
                <Link href={`/store/products/${l.slug}`} className="text-[19px] font-semibold tracking-[-0.02em] text-foreground hover:underline">{l.name}</Link>
                <p className="text-[15px] text-muted">{l.sku}{l.price && ` · ${formatMoney(l.price, info.currency)} each`}</p>
                {l.gone && <p className="mt-1.5 text-[15px] font-medium text-red-800" role="alert">This item is no longer available. Please remove it.</p>}
                {!l.gone && !l.inStock && <p className="mt-1.5 text-[15px] font-medium text-red-800" role="alert">Out of stock. Please remove it to continue.</p>}
              </div>
              <div className="col-span-2 flex items-center justify-between gap-5 sm:col-span-1 sm:justify-end sm:gap-7">
                <Stepper value={l.quantity} onCommit={(n) => setQty(l.sku, n)} label={`Quantity of ${l.name}`} size="sm" />
                <p className="w-20 text-right text-[19px] font-semibold tracking-[-0.02em]">{l.lineCents != null ? money(l.lineCents) : "—"}</p>
                <Button variant="ghost" size="sm" className="px-2.5" onClick={() => remove(l.sku)} aria-label={`Remove ${l.name}`}><Trash2 className="h-4 w-4" aria-hidden /></Button>
              </div>
            </li>
          ))}
        </ul>

        <Card className="space-y-6 p-7 lg:sticky lg:top-24">
          <h2 className="text-[24px]">Order summary</h2>
          {threshold > 0 && !Number.isNaN(threshold) && <FreeShippingMeter subtotalCents={est.subtotalCents} thresholdCents={threshold} />}
          <dl className="space-y-3 text-[17px]">
            <div className="flex justify-between"><dt className="text-muted">Subtotal</dt><dd>{money(est.subtotalCents)}</dd></div>
            <div className="flex justify-between"><dt className="text-muted">Shipping (estimate)</dt><dd>{money(est.shippingCents)}</dd></div>
            {info.sales_tax_mode === "STRIPE_TAX" && <div className="flex justify-between"><dt className="text-muted">Sales tax</dt><dd>At checkout</dd></div>}
            <div className="flex justify-between border-t border-[rgb(var(--color-border))] pt-4 text-[21px] font-semibold tracking-[-0.02em]"><dt>{info.sales_tax_mode === "STRIPE_TAX" ? "Estimated total before tax" : "Estimated total"}</dt><dd>{money(est.totalCents)}</dd></div>
          </dl>
          {est.blocked ? <Button size="lg" className="w-full" disabled>Checkout</Button> : <Link href="/store/checkout" className={buttonClass("primary", "lg", "w-full")}>Checkout</Link>}
          <p className="flex items-start gap-2 text-[13px] leading-relaxed text-muted"><Lock className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden /><span>{info.sales_tax_notice} The final total is confirmed when you check out.</span></p>
        </Card>
      </div>
    </div>
  );
}
