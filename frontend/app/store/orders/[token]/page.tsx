"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/Button";
import { StoreApiError, StoreOrder, getStoreOrder, payStoreOrder } from "@/lib/storeApi";
import { formatMoney } from "@/lib/storeCart";
import { friendlyProblem } from "@/lib/storeOrders";

const POLL_ATTEMPTS = 5;
const POLL_INTERVAL_MS = 2500;

export default function OrderPage() {
  return (
    <Suspense fallback={<p className="py-12 text-center text-muted" role="status">Loading your order…</p>}>
      <OrderInner />
    </Suspense>
  );
}

const safeLink = (url: string | null) => (url && /^https:\/\//i.test(url) ? url : null);

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
      <div className="py-12 text-center" role="alert">
        <h1 className="font-display text-3xl font-semibold tracking-tight text-foreground">{error.missing ? "We could not find this order" : "We could not load this order"}</h1>
        <p className="mt-3 text-muted">{error.missing ? "Check that you used the full link from your confirmation." : error.message}</p>
        <p className="mt-4"><Link href="/store" className="underline">Back to the store</Link></p>
      </div>
    );
  }
  if (!order) return <p className="py-12 text-center text-muted" role="status">Loading your order…</p>;

  const money = (a: string) => formatMoney(a, order.currency);
  const pending = order.status === "PENDING_PAYMENT";
  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <header>
        <h1 className="font-display text-3xl font-semibold tracking-tight text-foreground">Order {order.order_number}</h1>
        <p className="mt-1 text-lg text-foreground" data-testid="status">{order.status_label}</p>
      </header>
      {confirming && pending && <p role="status" className="rounded-2xl bg-surface-muted p-4 text-sm">Confirming your payment with the bank. This usually takes a few seconds…</p>}
      {!confirming && hint === "success" && pending && <p role="status" className="rounded-2xl bg-surface-muted p-4 text-sm">We have not received confirmation of your payment yet. This can take a minute: please refresh this page shortly. You will not be charged twice.</p>}
      {hint === "cancelled" && pending && <p role="status" className="rounded-2xl bg-surface-muted p-4 text-sm">No payment was taken. You can pay when you are ready.</p>}
      {payError.length > 0 && <div role="alert" className="rounded-2xl border border-danger/40 bg-red-50 p-4 text-sm text-red-800"><ul className="list-disc space-y-1 pl-5">{payError.map((p, i) => <li key={i}>{p}</li>)}</ul></div>}
      {order.can_pay && <Button onClick={payNow} disabled={paying}>{paying ? "Please wait…" : `Pay ${money(order.total)} now`}</Button>}

      <ul className="divide-y divide-border rounded-2xl border border-border bg-surface">
        {order.items.map((i, n) => <li key={n} className="flex justify-between gap-4 p-4 text-[15px]"><span>{i.quantity} × {i.name}</span><span>{money(i.line_total)}</span></li>)}
      </ul>
      <dl className="ml-auto grid max-w-xs grid-cols-2 gap-y-1 text-sm">
        <dt className="text-muted">Subtotal</dt><dd className="text-right">{money(order.subtotal)}</dd>
        <dt className="text-muted">Shipping</dt><dd className="text-right">{money(order.shipping)}</dd>
        <dt className="text-muted">Sales tax</dt><dd className="text-right">{money(order.tax)}</dd>
        <dt className="font-medium">Total</dt><dd className="text-right font-medium">{money(order.total)}</dd>
      </dl>
      {order.tracking.length > 0 && (
        <section aria-label="Tracking">
          <h2 className="mb-2 font-display text-xl font-semibold text-foreground">Tracking</h2>
          <ul className="space-y-1 text-[15px]">
            {order.tracking.map((t, n) => {
              const href = safeLink(t.tracking_url);
              return <li key={n}>{t.carrier ?? "Carrier"}: {href ? <a className="underline" href={href} target="_blank" rel="noreferrer">{t.tracking_number}</a> : t.tracking_number}</li>;
            })}
          </ul>
        </section>
      )}
      <p className="text-sm text-muted">Keep this page&apos;s link to check on your order. <Link href="/store" className="underline">Continue shopping</Link></p>
    </div>
  );
}
