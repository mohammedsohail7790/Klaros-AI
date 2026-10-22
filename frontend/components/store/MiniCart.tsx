"use client";

import { useEffect, useRef } from "react";
import Link from "next/link";
import { ShoppingBasket, Trash2, X } from "lucide-react";
import { ProductVisual } from "@/components/store/LabelTile";
import { usePricedCart, useStore } from "@/components/store/StoreProvider";
import { buttonClass } from "@/components/store/styles";
import { Button, FreeShippingMeter, Skeleton, Stepper } from "@/components/store/ui";
import { cartCount, estimate, formatMoney, fromCents, toCents } from "@/lib/storeCart";

/**
 * The basket as a panel that slides in from the right, so a shopper can add, change and check out without leaving the page they are on. It is a modal dialog: focus moves into it,
 * Tab stays inside, Escape or a click on the backdrop closes it, and the page behind does not scroll. Prices and stock are read live, only while it is open.
 */
export function MiniCart() {
  const { info, lines, ready, setQty, remove, cartOpen, closeCart } = useStore();
  const { priced, loading, error } = usePricedCart(lines, ready && cartOpen);
  const panel = useRef<HTMLDivElement>(null);
  const closeBtn = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!cartOpen) return;
    const before = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeBtn.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") return closeCart();
      if (e.key !== "Tab" || !panel.current) return;
      const focusable = [...panel.current.querySelectorAll<HTMLElement>("a[href], button:not([disabled]), input, [tabindex]:not([tabindex='-1'])")];
      if (focusable.length === 0) return;
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
      before?.focus?.();
    };
  }, [cartOpen, closeCart]);

  if (!cartOpen) return null;
  const n = cartCount(lines);
  const est = info ? estimate(info, priced) : null;
  const money = (c: number) => formatMoney(fromCents(c), info?.currency ?? "USD");
  const threshold = toCents(info?.free_shipping_over);

  return (
    <div className="fixed inset-0 z-[60]">
      <div className="cr-fade absolute inset-0 bg-[rgb(var(--color-foreground))]/45" onClick={closeCart} aria-hidden />
      <div ref={panel} role="dialog" aria-modal="true" aria-label="Basket" className="cr-slide-in absolute inset-y-0 right-0 flex w-[min(440px,100vw)] flex-col bg-background shadow-[0_0_60px_rgba(26,32,28,0.25)]">
        <div className="flex h-16 shrink-0 items-center justify-between border-b border-[rgb(var(--color-border))] px-5">
          <h2 className="text-[20px] font-semibold tracking-[-0.02em]">Your basket{n > 0 && <span className="ml-2 text-[15px] font-normal text-muted">{n} {n === 1 ? "item" : "items"}</span>}</h2>
          <button ref={closeBtn} type="button" onClick={closeCart} aria-label="Close basket" className="flex h-10 w-10 items-center justify-center rounded-full hover:bg-surface-muted"><X className="h-5 w-5" aria-hidden /></button>
        </div>

        {!ready || (loading && lines.length > 0) ? (
          <div role="status" aria-label="Loading your basket" className="flex-1 space-y-4 p-5"><Skeleton className="h-24" /><Skeleton className="h-24" /><span className="sr-only">Loading your basket…</span></div>
        ) : lines.length === 0 ? (
          <div className="flex flex-1 flex-col items-center justify-center px-8 text-center">
            <span className="flex h-16 w-16 items-center justify-center rounded-full bg-surface-muted"><ShoppingBasket className="h-7 w-7 text-[rgb(var(--color-brand))]" aria-hidden /></span>
            <p className="mt-5 text-[22px] font-semibold tracking-[-0.02em]">Your basket is empty</p>
            <p className="mt-2 text-[16px] text-muted">Add a few things and they will wait for you here.</p>
            <Link href="/store#products" onClick={closeCart} className={buttonClass("primary", "md", "mt-7")}>Browse products</Link>
          </div>
        ) : (
          <>
            {error && <p role="alert" className="px-5 pt-4 text-[14px] text-red-800">We could not check today&apos;s prices: {error}</p>}
            <ul className="flex-1 divide-y divide-[rgb(var(--color-border))] overflow-y-auto px-5">
              {priced.map((l) => (
                <li key={l.sku} aria-label={l.name} className="grid grid-cols-[72px_1fr] gap-x-4 gap-y-3 py-5">
                  <ProductVisual name={l.name} slug={l.slug} category={l.category} imageUrl={l.imageUrl} size="thumb" />
                  <div className="min-w-0">
                    <Link href={`/store/products/${l.slug}`} onClick={closeCart} className="line-clamp-2 text-[16px] font-semibold leading-snug tracking-[-0.01em] hover:underline">{l.name}</Link>
                    <p className="mt-0.5 text-[14px] text-muted">{l.price ? `${formatMoney(l.price, info?.currency)} each` : l.sku}</p>
                    {l.gone && <p role="alert" className="mt-1 text-[14px] font-medium text-red-800">No longer available. Please remove it.</p>}
                    {!l.gone && !l.inStock && <p role="alert" className="mt-1 text-[14px] font-medium text-red-800">Out of stock. Please remove it.</p>}
                  </div>
                  <div className="col-span-2 flex items-center justify-between">
                    <Stepper value={l.quantity} onCommit={(q) => setQty(l.sku, q)} label={`Quantity of ${l.name}`} size="sm" />
                    <div className="flex items-center gap-3">
                      <p className="text-[17px] font-semibold tracking-[-0.02em]">{l.lineCents != null ? money(l.lineCents) : "—"}</p>
                      <Button variant="ghost" size="sm" className="px-2" onClick={() => remove(l.sku)} aria-label={`Remove ${l.name}`}><Trash2 className="h-4 w-4" aria-hidden /></Button>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
            <div className="shrink-0 space-y-4 border-t border-[rgb(var(--color-border))] bg-background p-5">
              {est && threshold > 0 && !Number.isNaN(threshold) && <FreeShippingMeter subtotalCents={est.subtotalCents} thresholdCents={threshold} />}
              {est && (
                <dl className="space-y-1.5 text-[15px]">
                  <div className="flex justify-between"><dt className="text-muted">Subtotal</dt><dd>{money(est.subtotalCents)}</dd></div>
                  <div className="flex justify-between"><dt className="text-muted">Shipping (estimate)</dt><dd>{!est.shippingKnown ? "At checkout" : est.shippingCents === 0 ? "Free" : money(est.shippingCents)}</dd></div>
                  {info?.sales_tax_mode === "STRIPE_TAX" && <div className="flex justify-between"><dt className="text-muted">Sales tax</dt><dd>At checkout</dd></div>}
                </dl>
              )}
              {est?.tooHeavy && <p role="alert" className="text-[15px] font-medium text-red-800">This basket is heavier than we ship in one order. Please split it into smaller orders.</p>}
              {est?.blocked ? <Button size="lg" className="w-full" disabled>Checkout</Button> : <Link href="/store/checkout" onClick={closeCart} className={buttonClass("primary", "lg", "w-full")}>Checkout</Link>}
              <Link href="/store/cart" onClick={closeCart} className="block text-center text-[15px] text-[rgb(var(--color-brand))] underline-offset-4 hover:underline">View full basket</Link>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
