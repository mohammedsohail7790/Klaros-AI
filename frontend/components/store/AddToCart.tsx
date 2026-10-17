"use client";

import { useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/Button";
import { formatMoney } from "@/lib/storeCart";
import type { StoreProduct } from "@/lib/storeApi";
import { QtyInput } from "@/components/store/QtyInput";
import { useStore } from "@/components/store/StoreProvider";

/** Choose an item (if the product has several), a quantity, and add it to the basket. Out-of-stock items cannot be added. */
export function AddToCart({ product }: { product: StoreProduct }) {
  const { add } = useStore();
  const firstInStock = product.items.find((i) => i.in_stock) ?? product.items[0];
  const [sku, setSku] = useState(firstInStock?.sku ?? "");
  const [qty, setQty] = useState(1);
  const [added, setAdded] = useState(false);
  const item = product.items.find((i) => i.sku === sku);
  if (!item) return null;

  return (
    <div className="space-y-4">
      {product.items.length > 1 && (
        <label className="block text-sm">
          <span className="mb-1 block text-muted">Option</span>
          <select className="h-10 w-full rounded-xl bg-surface-muted px-3" value={sku} onChange={(e) => { setSku(e.target.value); setAdded(false); }}>
            {product.items.map((i) => <option key={i.sku} value={i.sku}>{i.sku} · {formatMoney(i.price, i.currency)}{i.in_stock ? "" : " (out of stock)"}</option>)}
          </select>
        </label>
      )}
      <p className="text-2xl font-semibold text-foreground">{formatMoney(item.price, item.currency)}</p>
      {item.in_stock ? (
        <div className="flex flex-wrap items-center gap-3">
          <label className="text-sm text-muted" htmlFor="qty">Quantity</label>
          <QtyInput id="qty" value={qty} onCommit={setQty} label="Quantity" />
          <Button onClick={() => { add({ sku: item.sku, slug: product.slug, name: product.name }, qty); setAdded(true); }}>Add to basket</Button>
        </div>
      ) : (
        <p className="text-danger">Out of stock right now.</p>
      )}
      {added && <p role="status" className="text-sm text-success">Added. <Link href="/store/cart" className="underline">View basket</Link></p>}
    </div>
  );
}
