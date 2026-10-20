"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Menu, Search, ShoppingBasket, X } from "lucide-react";
import { STORE_NAME } from "@/lib/storeConfig";
import { cartCount } from "@/lib/storeCart";
import { useStore } from "@/components/store/StoreProvider";

export function Wordmark({ className = "" }: { className?: string }) {
  return <Link href="/store" className={`text-[19px] font-semibold tracking-[-0.03em] ${className}`}>{STORE_NAME}</Link>;
}

const catHref = (c: string) => `/store?category=${encodeURIComponent(c)}#products`;

/**
 * The global bar, drawn like apple.com's: slim, translucent charcoal, the name on the left, the shop's categories in the middle, search and the basket on the right.
 * Search opens a large field under the bar; on a phone the categories sit behind a menu button. Sticky, so the basket is always one tap away.
 */
export function StoreHeader({ categories = [] }: { categories?: string[] }) {
  const { lines, ready } = useStore();
  const [panel, setPanel] = useState<"search" | "menu" | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const n = ready ? cartCount(lines) : 0;

  useEffect(() => {
    if (panel === "search") searchRef.current?.focus();
    if (!panel) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setPanel(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [panel]);

  const iconBtn = "flex h-9 w-9 items-center justify-center rounded-full text-[rgb(var(--color-background))]/90 hover:text-[rgb(var(--color-background))]";
  return (
    <header className="sticky top-0 z-40 text-[rgb(var(--color-background))]">
      <div className="cr-nav">
        <div className="mx-auto flex h-12 w-full max-w-[1080px] items-center justify-between gap-6 px-4 sm:px-6">
          <Wordmark />
          <nav aria-label="Store" className="hidden items-center gap-7 text-[13px] text-[rgb(var(--color-background))]/85 md:flex">
            <Link href="/store#products" className="hover:text-[rgb(var(--color-background))]">Shop</Link>
            {categories.slice(0, 5).map((c) => <Link key={c} href={catHref(c)} className="hover:text-[rgb(var(--color-background))]">{c}</Link>)}
          </nav>
          <div className="flex items-center gap-1">
            <button type="button" className={iconBtn} aria-label="Search" aria-expanded={panel === "search"} onClick={() => setPanel(panel === "search" ? null : "search")}>{panel === "search" ? <X className="h-[18px] w-[18px]" aria-hidden /> : <Search className="h-[18px] w-[18px]" aria-hidden />}</button>
            <Link href="/store/cart" aria-label={`Basket, ${n} ${n === 1 ? "item" : "items"}`} className={`${iconBtn} relative`}>
              <ShoppingBasket className="h-[19px] w-[19px]" aria-hidden />
              {n > 0 && <span key={n} className="cr-bump absolute -right-0.5 -top-0.5 flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-accent px-1 text-[11px] font-bold text-accent-foreground">{n}</span>}
            </Link>
            <button type="button" className={`${iconBtn} md:hidden`} aria-label="Menu" aria-expanded={panel === "menu"} onClick={() => setPanel(panel === "menu" ? null : "menu")}>{panel === "menu" ? <X className="h-[19px] w-[19px]" aria-hidden /> : <Menu className="h-[19px] w-[19px]" aria-hidden />}</button>
          </div>
        </div>
      </div>

      {panel === "search" && (
        <div className="cr-nav cr-fade border-t border-white/10">
          <form action="/store" method="get" role="search" className="mx-auto flex w-full max-w-[1080px] items-center gap-3 px-4 py-5 sm:px-6">
            <Search className="h-6 w-6 shrink-0 text-[rgb(var(--color-background))]/60" aria-hidden />
            <input ref={searchRef} type="search" name="q" aria-label="Search products" placeholder={`Search ${STORE_NAME}`} className="w-full bg-transparent text-[28px] font-semibold tracking-[-0.03em] text-[rgb(var(--color-background))] placeholder:text-[rgb(var(--color-background))]/40 focus-visible:!outline-none" />
          </form>
        </div>
      )}
      {panel === "menu" && (
        <nav aria-label="Menu" className="cr-nav cr-fade border-t border-white/10 md:hidden">
          <div className="mx-auto flex max-w-[1080px] flex-col px-4 py-4 sm:px-6">
            <Link href="/store#products" onClick={() => setPanel(null)} className="py-2.5 text-[28px] font-semibold tracking-[-0.03em]">All products</Link>
            {categories.map((c) => <Link key={c} href={catHref(c)} onClick={() => setPanel(null)} className="py-2.5 text-[28px] font-semibold tracking-[-0.03em] text-[rgb(var(--color-background))]/85">{c}</Link>)}
          </div>
        </nav>
      )}
    </header>
  );
}
