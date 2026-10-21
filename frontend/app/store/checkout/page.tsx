"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";
import { ChevronLeft, Lock } from "lucide-react";
import { StoreClosed } from "@/components/store/Footer";
import { ProductVisual } from "@/components/store/LabelTile";
import { usePricedCart, useStore } from "@/components/store/StoreProvider";
import { Button, Card, Input, Skeleton } from "@/components/store/ui";
import { StoreApiError, createCheckout } from "@/lib/storeApi";
import { cartFingerprint, estimate, formatMoney, fromCents } from "@/lib/storeCart";
import { checkoutKey, friendlyProblem, rememberOrder } from "@/lib/storeOrders";
import { SHIPPING_STATES } from "@/lib/usStates";

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const ZIP = /^\d{5}(-\d{4})?$/;

type Fields = { name: string; email: string; phone: string; line1: string; line2: string; city: string; state: string; zip: string };
const EMPTY: Fields = { name: "", email: "", phone: "", line1: "", line2: "", city: "", state: "", zip: "" };
const ORDER: (keyof Fields)[] = ["name", "email", "phone", "line1", "city", "state", "zip"];

function check(f: Fields): Partial<Record<keyof Fields, string>> {
  const e: Partial<Record<keyof Fields, string>> = {};
  if (!f.name.trim()) e.name = "Please enter your name.";
  if (!EMAIL.test(f.email.trim())) e.email = "Please enter a valid email address.";
  if (f.phone.trim() && f.phone.replace(/\D/g, "").length !== 10) e.phone = "A phone number, if given, needs 10 digits.";
  if (!f.line1.trim()) e.line1 = "Please enter your street address.";
  if (!f.city.trim()) e.city = "Please enter your city.";
  if (!f.state) e.state = "Please choose your state.";
  if (!ZIP.test(f.zip.trim())) e.zip = "Please enter a 5-digit ZIP code.";
  return e;
}

/** Contact and delivery details, then on to Stripe's own page to pay. The total is set by the store; card details are never typed here. */
export default function CheckoutPage() {
  const { info, lines, ready, clear } = useStore();
  const { priced, loading } = usePricedCart(lines, ready);
  const [f, setF] = useState<Fields>(EMPTY);
  const [errors, setErrors] = useState<Partial<Record<keyof Fields, string>>>({});
  const [refusal, setRefusal] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof Fields) => (e: { target: { value: string } }) => setF((p) => ({ ...p, [k]: e.target.value }));

  if (!info) return <StoreClosed />;
  if (!ready || loading) return <div role="status" aria-label="Loading checkout" className="grid gap-8 lg:grid-cols-[1fr_400px]"><Skeleton className="h-96" /><Skeleton className="h-72" /><span className="sr-only">Loading…</span></div>;
  if (lines.length === 0) return <p className="py-16 text-center text-lg">Your basket is empty. <Link href="/store" className="underline underline-offset-4">Browse products</Link></p>;
  const est = estimate(info, priced);
  if (est.blocked) return <p className="py-16 text-center text-lg" role="alert">Something in your basket is not available. <Link href="/store/cart" className="underline underline-offset-4">Go back to your basket</Link></p>;
  const money = (c: number) => formatMoney(fromCents(c), info.currency);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const found = check(f);
    setErrors(found);
    setRefusal([]);
    const first = ORDER.find((k) => found[k]);
    if (first) {
      document.getElementById(`co-${first}`)?.focus();
      return;
    }
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
      setRefusal(err instanceof StoreApiError ? [err.message, ...err.problems.map(friendlyProblem)] : ["Something went wrong. Please try again."]);
      setBusy(false);
    }
  }

  const messages = [...ORDER.map((k) => errors[k]).filter((m): m is string => !!m), ...refusal];
  return (
    <div>
      <Link href="/store/cart" className="mb-6 inline-flex items-center gap-1 text-[15px] text-[rgb(var(--color-brand))] hover:underline"><ChevronLeft className="h-4 w-4" aria-hidden /> Back to basket</Link>
      <h1 className="mb-10 text-[44px] sm:text-[56px]">Checkout</h1>
      <form onSubmit={submit} noValidate className="grid items-start gap-10 lg:grid-cols-[1fr_400px] lg:gap-16">
        <div className="space-y-12">
          {messages.length > 0 && (
            <div role="alert" className="rounded-2xl bg-red-50 p-5 text-[15px] text-red-900">
              <p className="font-semibold">Please check the following:</p>
              <ul className="mt-1.5 list-disc space-y-1 pl-5">{messages.map((m, i) => <li key={i}>{m}</li>)}</ul>
            </div>
          )}
          <section aria-label="Your details">
            <h2 className="mb-6 text-[28px]">Your details</h2>
            <div className="grid gap-4 sm:grid-cols-2">
              <Input id="co-name" label="Full name" value={f.name} onChange={set("name")} autoComplete="name" error={errors.name} wrapperClass="sm:col-span-2" />
              <Input id="co-email" label="Email" type="email" value={f.email} onChange={set("email")} autoComplete="email" error={errors.email} hint="We use this to contact you about your order." />
              <Input id="co-phone" label="Phone (optional)" type="tel" value={f.phone} onChange={set("phone")} autoComplete="tel" error={errors.phone} />
            </div>
          </section>
          <section aria-label="Delivery address">
            <h2 className="mb-1 text-[28px]">Delivery address</h2>
            <p className="mb-6 text-[17px] text-muted">We deliver to the continental United States.</p>
            <div className="grid gap-4 sm:grid-cols-6">
              <Input id="co-line1" label="Street address" value={f.line1} onChange={set("line1")} autoComplete="address-line1" error={errors.line1} wrapperClass="sm:col-span-6" />
              <Input id="co-line2" label="Apartment, suite (optional)" value={f.line2} onChange={set("line2")} autoComplete="address-line2" wrapperClass="sm:col-span-6" />
              <Input id="co-city" label="City" value={f.city} onChange={set("city")} autoComplete="address-level2" error={errors.city} wrapperClass="sm:col-span-3" />
              <div className="sm:col-span-2">
                <label htmlFor="co-state" className="mb-2 block text-sm font-medium text-foreground">State</label>
                <select id="co-state" value={f.state} onChange={set("state")} autoComplete="address-level1" aria-invalid={errors.state ? true : undefined} aria-describedby={errors.state ? "co-state-err" : undefined}
                  className={`h-[52px] w-full rounded-2xl bg-surface-muted px-4 text-[17px] focus-visible:ring-2 focus-visible:ring-[rgb(var(--color-brand))] ${errors.state ? "ring-2 ring-red-700" : ""}`}>
                  <option value="">Choose</option>
                  {SHIPPING_STATES.map((s) => <option key={s.code} value={s.code}>{s.name}</option>)}
                </select>
                {errors.state && <p id="co-state-err" className="mt-1.5 text-sm text-red-800">{errors.state}</p>}
              </div>
              <Input id="co-zip" label="ZIP code" value={f.zip} onChange={set("zip")} inputMode="numeric" autoComplete="postal-code" error={errors.zip} wrapperClass="sm:col-span-1" />
            </div>
          </section>
        </div>

        <Card className="space-y-6 p-7 lg:sticky lg:top-24">
          <h2 className="text-[24px]">Your order</h2>
          <ul className="space-y-3.5">
            {priced.map((l) => (
              <li key={l.sku} className="grid grid-cols-[56px_1fr_auto] items-center gap-4 text-[17px]">
                <ProductVisual name={l.name} slug={l.slug} category={l.category} imageUrl={l.imageUrl} size="thumb" tone="on-sage" />
                <span className="min-w-0"><span className="block truncate font-medium text-foreground">{l.name}</span><span className="text-sm text-muted">Quantity {l.quantity}</span></span>
                <span className="font-medium">{l.lineCents != null ? money(l.lineCents) : "—"}</span>
              </li>
            ))}
          </ul>
          <dl className="space-y-3 border-t border-[rgb(var(--color-border))] pt-5 text-[17px]">
            <div className="flex justify-between"><dt className="text-muted">Subtotal</dt><dd>{money(est.subtotalCents)}</dd></div>
            <div className="flex justify-between"><dt className="text-muted">Shipping (estimate)</dt><dd>{est.shippingKnown ? money(est.shippingCents) : "At checkout"}</dd></div>
            {info.sales_tax_mode === "STRIPE_TAX" && <div className="flex justify-between"><dt className="text-muted">Sales tax</dt><dd>At checkout</dd></div>}
            <div className="flex justify-between border-t border-[rgb(var(--color-border))] pt-4 text-[21px] font-semibold tracking-[-0.02em]"><dt>{est.shippingKnown ? (info.sales_tax_mode === "STRIPE_TAX" ? "Estimated total before tax" : "Estimated total") : "Subtotal before shipping and tax"}</dt><dd>{money(est.totalCents)}</dd></div>
          </dl>
          <Button type="submit" size="lg" className="w-full" disabled={busy}>{busy ? "Please wait…" : "Pay with card"}</Button>
          <p className="flex items-start gap-2 text-[13px] leading-relaxed text-muted"><Lock className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden /><span>{info.sales_tax_notice} You will pay on Stripe&apos;s secure page; we never see your card. The final total is shown there.</span></p>
        </Card>
      </form>
    </div>
  );
}
