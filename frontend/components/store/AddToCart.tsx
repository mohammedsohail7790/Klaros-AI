"use client";

import { useState } from "react";
import Link from "next/link";
import { Check, Plus } from "lucide-react";
import NotifyWhenBack from "@/components/store/NotifyWhenBack";
import { Button, Stepper, StockChip } from "@/components/store/ui";
import { StoreProduct } from "@/lib/storeApi";
import { cn } from "@/lib/cn";
import { formatMoney } from "@/lib/storeCart";
import { useStore } from "@/components/store/StoreProvider";

/** Price, stock, option, quantity and the one action. Out-of-stock items cannot be added. */
export function AddToCart({ product }: { product: StoreProduct }) {
  const { add } = useStore();
  const firstInStock = product.items.find((i) => i.in_stock) ?? product.items[0];
  const [sku, setSku] = useState(firstInStock?.sku ?? "");
  const [qty, setQty] = useState(1);
  const [added, setAdded] = useState(false);
  const item = product.items.find((i) => i.sku === sku);
  if (!item) return null;

  return (
    <div className="space-y-7">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <p className="text-[32px] font-semibold leading-none tracking-[-0.03em] text-foreground">{formatMoney(item.price, item.currency)}</p>
        <StockChip inStock={item.in_stock} />
      </div>

      {product.items.length > 1 && (
        <fieldset>
          <legend className="mb-3 text-[17px] font-semibold text-foreground">Choose an option.</legend>
          <div className="grid gap-3">
            {product.items.map((i) => (
              <label key={i.sku} className={cn("relative block", !i.in_stock && "opacity-55")}>
                <input type="radio" name="option" className="peer sr-only" value={i.sku} checked={sku === i.sku} onChange={() => { setSku(i.sku); setAdded(false); }} aria-label={`${i.sku}, ${formatMoney(i.price, i.currency)}${i.in_stock ? "" : ", out of stock"}`} />
                <span className="flex cursor-pointer items-center justify-between rounded-2xl border border-[rgb(var(--color-border))] px-5 py-4 text-[17px] hover:border-[rgb(var(--color-foreground))]/40 peer-checked:border-[rgb(var(--color-brand))] peer-checked:shadow-[inset_0_0_0_1px_rgb(var(--color-brand))] peer-focus-visible:outline peer-focus-visible:outline-4 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-[rgb(var(--color-accent))]">
                  <span className="font-semibold text-foreground">{i.sku}</span>
                  <span className="text-muted">{formatMoney(i.price, i.currency)}{!i.in_stock && " · sold out"}</span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>
      )}

      {item.in_stock ? (
        <div className="space-y-4">
          <div className="flex items-center gap-4">
            <span className="text-[17px] font-semibold text-foreground" aria-hidden>Quantity</span>
            <Stepper id="qty" value={qty} onCommit={setQty} label="Quantity" />
          </div>
          <Button size="lg" className="w-full" onClick={() => { add({ sku: item.sku, slug: product.slug, name: product.name }, qty); setAdded(true); }}>
            <Plus className="h-5 w-5" aria-hidden /> Add to basket
          </Button>
        </div>
      ) : (
        <div className="space-y-4">
          <p className="rounded-2xl bg-surface-muted px-5 py-4 text-foreground">Out of stock right now.</p>
          <NotifyWhenBack key={item.sku} sku={item.sku} />
        </div>
      )}

      {added && (
        <p role="status" className="cr-fade flex items-center gap-2 text-[17px] text-[rgb(var(--color-brand))]">
          <Check className="h-5 w-5" aria-hidden /> Added. <Link href="/store/cart" className="underline underline-offset-4">View basket</Link>
        </p>
      )}
    </div>
  );
}
