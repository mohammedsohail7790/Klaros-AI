"use client";

import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Input";
import type { StoreSettings } from "@/lib/dropshippingApi";

type Row = { max: string; fee: string };

/** The shipping rule: one flat fee per order, or tiers by the weight of the order (the way the supplier charges). Orders at or over the free-shipping amount ship free either way. */
export default function ShippingRule({ settings, manage, busy, onSave }: { settings: StoreSettings; manage: boolean; busy: boolean; onSave: (changes: Partial<StoreSettings>) => void }) {
  const hasTiers = !!settings.shipping_tiers && settings.shipping_tiers.length > 0;
  const [mode, setMode] = useState<"FLAT" | "TIERS">(hasTiers ? "TIERS" : "FLAT");
  const [fee, setFee] = useState(settings.shipping_flat_fee ?? "");
  const [freeOver, setFreeOver] = useState(settings.free_shipping_over ?? "");
  const [rows, setRows] = useState<Row[]>(hasTiers ? settings.shipping_tiers!.map((t) => ({ max: t.max_lb ?? "", fee: t.fee })) : [{ max: "", fee: "" }]);
  const free = freeOver.trim() === "" ? null : freeOver.trim();
  const setRow = (i: number, over: Partial<Row>) => setRows(rows.map((r, n) => (n === i ? { ...r, ...over } : r)));
  const tiersReady = rows.length > 0 && rows.every((r) => r.fee.trim() !== "");

  return (
    <section aria-label="Shipping" className="space-y-4">
      <h2 className="font-display text-[1.5rem] font-semibold tracking-[-0.02em] text-foreground">Shipping</h2>
      <fieldset className="space-y-3" disabled={!manage || busy}>
        <legend className="sr-only">How shipping is priced</legend>
        <label className="flex items-start gap-3 text-[15px]"><input type="radio" name="ship-mode" className="mt-1" checked={mode === "FLAT"} onChange={() => setMode("FLAT")} /><span><strong>One flat fee per order.</strong></span></label>
        <label className="flex items-start gap-3 text-[15px]"><input type="radio" name="ship-mode" className="mt-1" checked={mode === "TIERS"} onChange={() => setMode("TIERS")} /><span><strong>By the weight of the order.</strong> Tiers, like the supplier&apos;s. Every item needs a weight in the catalog.</span></label>
      </fieldset>

      {mode === "FLAT" ? (
        <Field label="Flat shipping fee (0 for free shipping)"><Input inputMode="decimal" value={fee} onChange={(e) => setFee(e.target.value)} placeholder="5.00" disabled={!manage} /></Field>
      ) : (
        <div className="space-y-3">
          <p className="text-sm text-muted">Each tier covers orders up to its weight, in pounds (a weight on the limit belongs to that tier). Leave the last limit empty for every heavier order; without such a tier, heavier orders cannot be placed.</p>
          <ul className="space-y-2" aria-label="Shipping tiers">
            {rows.map((r, i) => (
              <li key={i} className="grid grid-cols-[1fr_1fr_auto] items-end gap-3">
                <Field label={i === 0 ? "Up to (lb)" : `Up to (lb), tier ${i + 1}`}><Input inputMode="decimal" value={r.max} onChange={(e) => setRow(i, { max: e.target.value })} placeholder={i === rows.length - 1 ? "no limit" : "5"} disabled={!manage} /></Field>
                <Field label={i === 0 ? "Fee ($)" : `Fee ($), tier ${i + 1}`}><Input inputMode="decimal" value={r.fee} onChange={(e) => setRow(i, { fee: e.target.value })} placeholder="9.95" disabled={!manage} /></Field>
                {manage && rows.length > 1 && <Button variant="ghost" aria-label={`Remove tier ${i + 1}`} onClick={() => setRows(rows.filter((_, n) => n !== i))}><Trash2 className="h-4 w-4" aria-hidden /></Button>}
              </li>
            ))}
          </ul>
          {manage && <Button variant="secondary" onClick={() => setRows([...rows, { max: "", fee: "" }])}><Plus className="h-4 w-4" aria-hidden /> Add a tier</Button>}
        </div>
      )}

      <Field label="Free shipping from (optional)"><Input inputMode="decimal" value={freeOver} onChange={(e) => setFreeOver(e.target.value)} placeholder="50.00" disabled={!manage} /></Field>
      {manage && (
        mode === "FLAT"
          ? <Button disabled={busy || fee.trim() === ""} onClick={() => onSave({ shipping_tiers: null, shipping_flat_fee: fee.trim(), free_shipping_over: free })}>Save shipping</Button>
          : <Button disabled={busy || !tiersReady} onClick={() => onSave({ shipping_tiers: rows.map((r) => ({ max_lb: r.max.trim() === "" ? null : r.max.trim(), fee: r.fee.trim() })), free_shipping_over: free })}>Save shipping</Button>
      )}
    </section>
  );
}
