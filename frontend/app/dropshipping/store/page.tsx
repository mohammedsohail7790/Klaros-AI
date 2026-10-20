"use client";

import { useCallback, useEffect, useState } from "react";
import { Settings2 } from "lucide-react";
import AppShell from "@/components/AppShell";
import { ProblemAlert } from "@/components/dropshipping/parts";
import SupplierSync from "@/components/dropshipping/SupplierSync";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { StoreView, canManage, describeError, getStoreSettings, putStoreSettings } from "@/lib/dropshippingApi";
import { useAuth } from "@/lib/useAuth";

/** When the public store is open: the shipping rule, the sales-tax acknowledgement and the switch. The server refuses to open the store while anything is missing. */
export default function DropshippingStoreSettingsPage() {
  const { token, user, loading: authLoading } = useAuth();
  const manage = canManage(user?.role);
  const [view, setView] = useState<StoreView | null>(null);
  const [fee, setFee] = useState("");
  const [freeOver, setFreeOver] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<{ message: string; problems: string[] } | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  const apply = (v: StoreView) => {
    setView(v);
    setFee(v.settings.shipping_flat_fee ?? "");
    setFreeOver(v.settings.free_shipping_over ?? "");
  };

  const load = useCallback(async () => {
    if (!token) return;
    setError(null);
    try {
      apply(await getStoreSettings(token));
    } catch (err) {
      setError(describeError(err, "Unable to load the store settings.").message);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function save(changes: Parameters<typeof putStoreSettings>[1]) {
    if (!token) return;
    setBusy(true);
    setRefusal(null);
    setSaved(false);
    try {
      apply(await putStoreSettings(token, changes));
      setSaved(true);
    } catch (err) {
      setRefusal(describeError(err, "That change was refused."));
    } finally {
      setBusy(false);
    }
  }

  const s = view?.settings;
  return (
    <AppShell user={user}>
      <div className="mx-auto w-full max-w-3xl px-5 py-8 sm:px-8 sm:py-12">
        <PageHeader title="Store settings" description="When shoppers can buy, and what shipping costs them." icon={Settings2} />
        {authLoading || (!view && !error) ? (
          <Skeleton />
        ) : error ? (
          <Alert variant="danger" action={<Button size="sm" variant="secondary" onClick={() => load()}>Try again</Button>}>{error}</Alert>
        ) : view && s ? (
          <div className="space-y-8">
            {view.open_to_shoppers ? (
              <Alert variant="success">The store is open to shoppers.</Alert>
            ) : (
              <Alert variant="warning">
                <div>The store is closed to shoppers.</div>
                {view.blockers.length > 0 && <ul className="mt-2 list-disc space-y-1 pl-5">{view.blockers.map((b) => <li key={b}>{b}</li>)}</ul>}
              </Alert>
            )}
            {!manage && <Alert variant="info">You can look at these settings. Only an owner or manager can change them.</Alert>}
            {refusal && <ProblemAlert message={refusal.message} problems={refusal.problems} />}
            {saved && <Alert variant="success">Saved.</Alert>}

            <section aria-label="Shipping" className="space-y-4">
              <h2 className="font-display text-[1.5rem] font-semibold tracking-[-0.02em] text-foreground">Shipping</h2>
              <p className="text-sm text-muted">One flat fee per order, in US dollars. Orders at or over the free-shipping amount ship free. Shoppers cannot change either.</p>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Flat shipping fee (0 for free shipping)"><Input inputMode="decimal" value={fee} onChange={(e) => setFee(e.target.value)} placeholder="5.00" disabled={!manage} /></Field>
                <Field label="Free shipping from (optional)"><Input inputMode="decimal" value={freeOver} onChange={(e) => setFreeOver(e.target.value)} placeholder="50.00" disabled={!manage} /></Field>
              </div>
              {manage && (
                <Button disabled={busy || fee.trim() === ""} onClick={() => save({ shipping_flat_fee: fee.trim(), free_shipping_over: freeOver.trim() === "" ? null : freeOver.trim() })}>Save shipping</Button>
              )}
            </section>

            <section aria-label="Sales tax" className="space-y-3">
              <h2 className="font-display text-[1.5rem] font-semibold tracking-[-0.02em] text-foreground">Sales tax</h2>
              <p className="text-sm text-muted">Orders are recorded with no sales tax and the store tells shoppers that tax is not collected. Confirm that you have decided this is acceptable before opening the store.</p>
              <label className="flex items-start gap-3 text-[15px]">
                <input type="checkbox" className="mt-1" checked={s.sales_tax_acknowledged} disabled={!manage || busy} onChange={(e) => save({ sales_tax_acknowledged: e.target.checked })} />
                <span>I understand sales tax is not collected on these orders.</span>
              </label>
            </section>

            <section aria-label="Open the store" className="space-y-3">
              <h2 className="font-display text-[1.5rem] font-semibold tracking-[-0.02em] text-foreground">Open the store</h2>
              <p className="text-sm text-muted">The switch can only be turned on when a shipping rule is set, tax is acknowledged, Stripe is connected and a product is on sale. Closing it stops new orders at once.</p>
              {manage && (s.open ? <Button variant="secondary" disabled={busy} onClick={() => save({ open: false })}>Close the store</Button> : <Button disabled={busy} onClick={() => save({ open: true })}>Open the store</Button>)}
            </section>

            {token && <SupplierSync token={token} manage={manage} />}
          </div>
        ) : null}
      </div>
    </AppShell>
  );
}
