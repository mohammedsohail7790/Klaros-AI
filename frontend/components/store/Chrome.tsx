"use client";

import Link from "next/link";
import { ShoppingBasket } from "lucide-react";
import { STORE_NAME } from "@/lib/storeConfig";
import { cartCount } from "@/lib/storeCart";
import { useStore } from "@/components/store/StoreProvider";

export function StoreHeader() {
  const { lines, ready } = useStore();
  const n = ready ? cartCount(lines) : 0;
  return (
    <header className="border-b border-border bg-surface">
      <div className="mx-auto flex w-full max-w-6xl items-center justify-between px-5 py-4 sm:px-8">
        <Link href="/store" className="font-display text-xl font-semibold tracking-tight text-foreground">{STORE_NAME}</Link>
        <Link href="/store/cart" className="inline-flex items-center gap-2 rounded-full bg-surface-muted px-4 py-2 text-sm font-medium text-foreground" aria-label={`Basket, ${n} ${n === 1 ? "item" : "items"}`}>
          <ShoppingBasket className="h-4 w-4" aria-hidden /> Basket{n > 0 && <span className="rounded-full bg-accent px-2 text-xs text-accent-foreground">{n}</span>}
        </Link>
      </div>
    </header>
  );
}
