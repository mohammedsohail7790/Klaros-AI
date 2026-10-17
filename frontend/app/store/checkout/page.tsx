"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/Button";
import { StoreClosed } from "@/components/store/Footer";
import { usePricedCart, useStore } from "@/components/store/StoreProvider";
import { StoreApiError, createCheckout } from "@/lib/storeApi";
import { cartFingerprint, estimate, formatMoney, fromCents } from "@/lib/storeCart";
import { checkoutKey, friendlyProblem, rememberOrder } from "@/lib/storeOrders";
import { SHIPPING_STATES } from "@/lib/usStates";

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const ZIP = /^\d{5}(-\d{4})?$/;

const FIELD = "h-11 w-full rounded-xl bg-surface-muted px-3 text-foreground";

/** Contact and delivery details, then on to Stripe's own page to pay. The total is set by the store; card details are never typed here. */
export default function CheckoutPage() {
  const { info, lines, ready, clear } = useStore();
  const { priced, loading } = usePricedCart(lines, ready);
  const [f, setF] = useState({ name: "", email: "", phone: "", line1: "", line2: "", city: "", state: "", zip: "" });
  const [problems, setProblems] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF((p) => ({ ...p, [k]: e.target.value }));

  if (!info) return <StoreClosed />;
  if (!ready || loading) return <p className="py-12 text-center text-muted" role="status">Loading…</p>;
  if (lines.length === 0) return <p className="py-12 text-center">Your basket is empty. <Link href="/store" className="underline">Browse products</Link></p>;
  const est = estimate(info, priced);
  if (est.blocked) return <p className="py-12 text-center" role="alert">Something in your basket is not available. <Link href="/store/cart" className="underline">Go back to your basket</Link></p>;
  const money = (c: number) => formatMoney(fromCents(c), info.currency);

  function check(): string[] {
    const out: string[] = [];
    if (!f.name.trim()) out.push("Please enter your name.");
    if (!EMAIL.test(f.email.trim())) out.push("Please enter a valid email address.");
    if (f.phone.trim() && f.phone.replace(/\D/g, "").length !== 10) out.push("A phone number, if given, needs 10 digits.");
    if (!f.line1.trim()) out.push("Please enter your street address.");
    if (!f.city.trim()) out.push("Please enter your city.");
    if (!f.state) out.push("Please choose your state.");
    if (!ZIP.test(f.zip.trim())) out.push("Please enter a 5-digit ZIP code.");
    return out;
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    const found = check();
    setProblems(found);
    if (found.length > 0) return;
    setBusy(true);
    try {
      const result = await createCheckout({
        lines: lines.map((l) => ({ sku: l.sku, quantity: l.quantity })),
        contact: { name: f.name.trim(), email: f.email.trim(), phone: f.phone.trim() || undefined },
        ship_to: { name: f.name.trim(), line1: f.line1.trim(), line2: f.line2.trim() || undefined, city: f.city.trim(), state: f.state, postal_code: f.zip.trim() },
        idempotency_key: checkoutKey(cartFingerprint(lines)),
      });
      rememberOrder(result.status_token, result.order_number);
      clear();                                    // the order exists and can always be paid from its page
      window.location.assign(result.checkout_url ?? `/store/orders/${result.status_token}`);
    } catch (err) {
      if (err instanceof StoreApiError) setProblems([err.message, ...err.problems.map(friendlyProblem)]);
      else setProblems(["Something went wrong. Please try again."]);
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} noValidate className="mx-auto max-w-2xl space-y-6">
      <h1 className="font-display text-3xl font-semibold tracking-tight text-foreground">Checkout</h1>
      {problems.length > 0 && (
        <div role="alert" className="rounded-2xl border border-danger/40 bg-red-50 p-4 text-sm text-red-800">
          <p className="font-medium">Please check the following:</p>
          <ul className="mt-1 list-disc space-y-1 pl-5">{problems.map((p, i) => <li key={i}>{p}</li>)}</ul>
        </div>
      )}
      <fieldset className="grid gap-4 sm:grid-cols-2">
        <legend className="mb-2 font-medium">Your details</legend>
        <label className="text-sm">Full name<input className={FIELD} value={f.name} onChange={set("name")} autoComplete="name" /></label>
        <label className="text-sm">Email<input className={FIELD} type="email" value={f.email} onChange={set("email")} autoComplete="email" /></label>
        <label className="text-sm">Phone (optional)<input className={FIELD} type="tel" value={f.phone} onChange={set("phone")} autoComplete="tel" /></label>
      </fieldset>
      <fieldset className="grid gap-4 sm:grid-cols-2">
        <legend className="mb-2 font-medium">Delivery address (continental United States)</legend>
        <label className="text-sm sm:col-span-2">Street address<input className={FIELD} value={f.line1} onChange={set("line1")} autoComplete="address-line1" /></label>
        <label className="text-sm sm:col-span-2">Apartment, suite (optional)<input className={FIELD} value={f.line2} onChange={set("line2")} autoComplete="address-line2" /></label>
        <label className="text-sm">City<input className={FIELD} value={f.city} onChange={set("city")} autoComplete="address-level2" /></label>
        <label className="text-sm">State
          <select className={FIELD} value={f.state} onChange={set("state")} autoComplete="address-level1">
            <option value="">Choose a state</option>
            {SHIPPING_STATES.map((s) => <option key={s.code} value={s.code}>{s.name}</option>)}
          </select>
        </label>
        <label className="text-sm">ZIP code<input className={FIELD} value={f.zip} onChange={set("zip")} inputMode="numeric" autoComplete="postal-code" /></label>
      </fieldset>
      <dl className="grid max-w-xs grid-cols-2 gap-y-1 text-sm">
        <dt className="text-muted">Subtotal</dt><dd className="text-right">{money(est.subtotalCents)}</dd>
        <dt className="text-muted">Shipping (estimate)</dt><dd className="text-right">{money(est.shippingCents)}</dd>
        <dt className="font-medium">Estimated total</dt><dd className="text-right font-medium">{money(est.totalCents)}</dd>
      </dl>
      <p className="text-xs text-muted">{info.sales_tax_notice} You will pay on Stripe&apos;s secure page; we never see your card. The final total is shown there.</p>
      <div className="flex items-center justify-between">
        <Link href="/store/cart" className="text-sm text-muted underline">Back to basket</Link>
        <Button type="submit" disabled={busy}>{busy ? "Please wait…" : "Pay with card"}</Button>
      </div>
    </form>
  );
}
