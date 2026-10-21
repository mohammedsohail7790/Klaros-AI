"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { CartLine, addLine, parseCart, priceLines, removeLine, setQuantity, PricedLine } from "@/lib/storeCart";
import { StoreInfo, StoreProduct, getStoreProduct } from "@/lib/storeApi";

const CART_KEY = "klaros_store_cart";

interface Ctx {
  info: StoreInfo | null;
  lines: CartLine[];
  /** false until the saved cart has been read in the browser, so a page never flashes "empty basket" */
  ready: boolean;
  add: (item: Omit<CartLine, "quantity">, quantity?: number) => void;
  setQty: (sku: string, quantity: number) => void;
  remove: (sku: string) => void;
  clear: () => void;
  /** the slide-out basket */
  cartOpen: boolean;
  openCart: () => void;
  closeCart: () => void;
}
const StoreContext = createContext<Ctx | null>(null);

/** The store's public info (from the server) and the shopper's basket, kept in this browser only. */
export function StoreProvider({ info, children }: { info: StoreInfo | null; children: React.ReactNode }) {
  const [lines, setLines] = useState<CartLine[]>([]);
  const [ready, setReady] = useState(false);
  const [cartOpen, setCartOpen] = useState(false);

  useEffect(() => {
    try {
      setLines(parseCart(localStorage.getItem(CART_KEY)));
    } catch {
      // storage can be blocked; the basket then lasts for this visit only
    }
    setReady(true);
  }, []);

  const save = useCallback((next: CartLine[]) => {
    setLines(next);
    try {
      localStorage.setItem(CART_KEY, JSON.stringify(next));
    } catch {
      // best-effort only
    }
  }, []);

  const value = useMemo<Ctx>(
    () => ({
      info, lines, ready,
      add: (item, quantity = 1) => save(addLine(lines, item, quantity)),
      setQty: (sku, quantity) => save(setQuantity(lines, sku, quantity)),
      remove: (sku) => save(removeLine(lines, sku)),
      clear: () => save([]),
      cartOpen, openCart: () => setCartOpen(true), closeCart: () => setCartOpen(false),
    }),
    [info, lines, ready, save, cartOpen]
  );
  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}

export function useStore(): Ctx {
  const ctx = useContext(StoreContext);
  if (!ctx) throw new Error("useStore must be used inside the store layout");
  return ctx;
}

/** The basket with today's prices and stock, read live from the store. A product the store no longer has comes back as `gone`. */
export function usePricedCart(lines: CartLine[], ready: boolean): { priced: PricedLine[]; loading: boolean; error: string | null } {
  const [products, setProducts] = useState<Map<string, StoreProduct | null> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const slugs = useMemo(() => [...new Set(lines.map((l) => l.slug))].sort().join(","), [lines]);

  useEffect(() => {
    if (!ready) return;
    if (!slugs) {
      setProducts(new Map());
      return;
    }
    let cancelled = false;
    setError(null);
    Promise.all(slugs.split(",").map(async (s) => [s, await getStoreProduct(s, { live: true })] as const))
      .then((pairs) => !cancelled && setProducts(new Map(pairs)))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : "Prices could not be loaded."));
    return () => {
      cancelled = true;
    };
  }, [slugs, ready]);

  const priced = useMemo(() => (products ? priceLines(lines, products) : []), [lines, products]);
  return { priced, loading: !ready || (products === null && !error), error };
}
