"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Check, ExternalLink, Info, Package } from "lucide-react";
import { buttonClass } from "@/components/store/styles";
import { Button, Card, Skeleton } from "@/components/store/ui";
import { cn } from "@/lib/cn";
import { StoreApiError, StoreOrder, getStoreOrder, payStoreOrder } from "@/lib/storeApi";
import { formatMoney } from "@/lib/storeCart";
import { friendlyProblem } from "@/lib/storeOrders";

const POLL_ATTEMPTS = 5;
const POLL_INTERVAL_MS = 2500;

export default function OrderPage() {
  return (
    <Suspense fallback={<div role="status" aria-label="Loading your order" className="mx-auto max-w-3xl space-y-4"><Skeleton className="h-12 w-72" /><Skeleton className="h-28" /><Skeleton className="h-48" /><span className="sr-only">Loading your order…</span></div>}>
      <OrderInner />
    </Suspense>
  );
}

const safeLink = (url: string | null) => (url && /^https:\/\//i.test(url) ? url : null);

/** How far an order has got. Cancelled and refunded orders are not on this path: they are shown as a notice instead. */
const STEPS = ["Order placed", "Payment received", "Being prepared", "Shipped", "Delivered"];
const LEVEL: Record<string, number> = { PENDING_PAYMENT: 0, PAID: 1, SUPPLIER_ORDER_PLACED: 2, SHIPPED: 3, DELIVERED: 4 };

function Progress({ status }: { status: string }) {
  const level = LEVEL[status];
  if (level === undefined) return null;
  return (
    <ol aria-label="Order progress" className="grid grid-cols-5 gap-1.5">
      {STEPS.map((label, i) => {
        const done = i <= level;
        const current = i === level + 1 && level < 4;
        return (
          <li key={label} aria-current={current ? "step" : undefined} className="flex flex-col items-center gap-2 text-center">
            <span className={cn("flex h-8 w-8 items-center justify-center rounded-full text-sm font-semibold", done ? "bg-[rgb(var(--color-brand))] text-[rgb(var(--color-background))]" : current ? "border-2 border-accent text-foreground"   : "bg-background text-muted")}>
              {done ? <Check className="h-4 w-4" aria-hidden /> : i + 1}
            </span>
            <span className={cn("text-[13px] leading-tight", done || current ? "font-medium text-foreground" : "text-muted")}>{label}</span>
            <span className="sr-only">{done ? "done" : current ? "next" : "to come"}</span>
          </li>
        );
      })}
    </ol>
  );
}

/**
 * The shopper's own order page, found by its private link. It never shows an address, email, phone or payment reference (the store does not send them).
 * `?payment=success` is only a hint from Stripe's redirect, never proof: "Paid" is shown only when the store itself says so, after checking a few times.
 */
function OrderInner() {
  const { token } = useParams<{ token: string }>();
  const hint = useSearchParams().get("payment");
  const [order, setOrder] = useState<StoreOrder | null>(null);
  const [error, setError] = useState<{ message: string; missing: boolean } | null>(null);
  const [confirming, setConfirming] = useState(hint === "success");
  const [payError, setPayError] = useState<string[]>([]);
  const [paying, setPaying] = useState(false);
  const attempts = useRef(0);

  const load = useCallback(async () => {
    try {
      const o = await getStoreOrder(token);
      setOrder(o);
      setError(null);
      return o;
    } catch (err) {
      setError({ message: err instanceof StoreApiError ? err.message : "We could not load this order.", missing: err instanceof StoreApiError && err.status === 404 });
      return null;
    }
  }, [token]);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      const o = await load();
      if (cancelled) return;
      if (hint === "success" && o && o.status === "PENDING_PAYMENT" && attempts.current < POLL_ATTEMPTS) {
        attempts.current += 1;
        timer = setTimeout(tick, POLL_INTERVAL_MS);
      } else {
        setConfirming(false);
      }
    };
    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [load, hint]);

  async function payNow() {
    setPaying(true);
    setPayError([]);
    try {
      const r = await payStoreOrder(token);
      if (r.checkout_url) window.location.assign(r.checkout_url);
      else await load();
    } catch (err) {
      setPayError(err instanceof StoreApiError ? [err.message, ...err.problems.map(friendlyProblem)] : ["Something went wrong. Please try again."]);
    } finally {
      setPaying(false);
    }
  }

  if (error && !order) {
    return (
      <div className="mx-auto max-w-md py-16 text-center" role="alert">
        <h1 className="text-[40px]">{error.missing ? "We could not find this order" : "We could not load this order"}</h1>
        <p className="mt-3 text-muted">{error.missing ? "Check that you used the full link from your confirmation." : error.message}</p>
        <p className="mt-7"><Link href="/store" className={buttonClass("primary", "md")}>Back to the store</Link></p>
      </div>
    );
  }
  if (!order) return <div role="status" aria-label="Loading your order" className="mx-auto max-w-3xl space-y-4"><Skeleton className="h-12 w-72" /><Skeleton className="h-28" /><Skeleton className="h-48" /><span className="sr-only">Loading your order…</span></div>;

  const money = (a: string) => formatMoney(a, order.currency);
  const pending = order.status === "PENDING_PAYMENT";
  const Notice = ({ children }: { children: React.ReactNode }) => <p role="status" className="flex items-start gap-2.5 rounded-xl bg-surface-muted p-4 text-sm"><Info className="mt-0.5 h-4 w-4 shrink-0 text-[rgb(var(--color-brand))]" aria-hidden />{children}</p>;
  return (
    <div className="mx-auto max-w-3xl space-y-7">
      <header>
        <h1 className="text-[40px] sm:text-[56px]">Order {order.order_number}</h1>
        <p className="mt-3 text-[24px] tracking-[-0.02em] text-foreground" data-testid="status">{order.status_label}</p>
      </header>

      {confirming && pending && <Notice>Confirming your payment with the bank. This usually takes a few seconds…</Notice>}
      {!confirming && hint === "success" && pending && <Notice>We have not received confirmation of your payment yet. This can take a minute: please refresh this page shortly. You will not be charged twice.</Notice>}
      {hint === "cancelled" && pending && <Notice>No payment was taken. You can pay when you are ready.</Notice>}
      {payError.length > 0 && <div role="alert" className="rounded-xl border border-red-300 bg-red-50 p-4 text-sm text-red-900"><ul className="list-disc space-y-1 pl-5">{payError.map((p, i) => <li key={i}>{p}</li>)}</ul></div>}

      {order.can_pay && (
        <div className="flex flex-wrap items-center justify-between gap-5 rounded-[28px] bg-surface-muted p-6 sm:p-8">
          <div><p className="text-[21px] font-semibold tracking-[-0.02em] text-foreground">This order is waiting for payment.</p><p className="text-[15px] text-muted">You pay on Stripe&apos;s secure page.</p></div>
          <Button size="lg" onClick={payNow} disabled={paying}>{paying ? "Please wait…" : `Pay ${money(order.total)} now`}</Button>
        </div>
      )}

      <Card className="p-6 sm:p-8"><Progress status={order.status} />{LEVEL[order.status] === undefined && <p className="font-medium">{order.status_label}</p>}</Card>

      <div className="grid gap-6 md:grid-cols-[1.4fr_1fr]">
        <Card className="overflow-hidden">
          <h2 className="px-6 pb-2 pt-6 text-[24px]">Items</h2>
          <ul className="divide-y divide-[rgb(var(--color-border))] px-6 pb-3">
            {order.items.map((i, n) => <li key={n} className="flex justify-between gap-4 py-3.5 text-[15px]"><span>{i.quantity} × {i.name}</span><span className="font-medium">{money(i.line_total)}</span></li>)}
          </ul>
        </Card>
        <Card className="self-start p-6">
          <h2 className="mb-4 text-[24px]">Totals</h2>
          <dl className="space-y-2.5 text-[15px]">
            <div className="flex justify-between"><dt className="text-muted">Subtotal</dt><dd>{money(order.subtotal)}</dd></div>
            <div className="flex justify-between"><dt className="text-muted">Shipping</dt><dd>{money(order.shipping)}</dd></div>
            <div className="flex justify-between"><dt className="text-muted">Sales tax</dt><dd>{order.tax_at_payment ? "Added when you pay" : money(order.tax)}</dd></div>
            <div className="flex justify-between border-t border-[rgb(var(--color-border))] pt-3 text-[19px] font-semibold"><dt>Total</dt><dd>{money(order.total)}</dd></div>
          </dl>
        </Card>
      </div>

      {order.tracking.length > 0 && (
        <Card className="p-6 sm:p-7" role="region" aria-label="Tracking">
          <h2 className="mb-3 flex items-center gap-2 text-[24px]"><Package className="h-5 w-5 text-[rgb(var(--color-brand))]" aria-hidden /> Tracking</h2>
          <ul className="space-y-2 text-[15px]">
            {order.tracking.map((t, n) => {
              const href = safeLink(t.tracking_url);
              return <li key={n}>{t.carrier ?? "Carrier"}: {href ? <a className="inline-flex items-center gap-1 font-medium underline underline-offset-4" href={href} target="_blank" rel="noreferrer">{t.tracking_number}<ExternalLink className="h-3.5 w-3.5" aria-hidden /></a> : t.tracking_number}</li>;
            })}
          </ul>
        </Card>
      )}
      <p className="text-sm text-muted">Keep this page&apos;s link to check on your order. <Link href="/store" className="underline underline-offset-4">Continue shopping</Link></p>
    </div>
  );
}
