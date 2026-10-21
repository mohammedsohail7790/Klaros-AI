"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { Menu, Search, ShoppingBasket, Truck, X } from "lucide-react";
import { MiniCart } from "@/components/store/MiniCart";
import { useStore } from "@/components/store/StoreProvider";
import { STORE_NAME } from "@/lib/storeConfig";
import { cartCount } from "@/lib/storeCart";
import { cn } from "@/lib/cn";

export function Wordmark({ className = "" }: { className?: string }) {
  return <Link href="/store/welcome" className={cn("text-[22px] font-semibold tracking-[-0.04em] text-[rgb(var(--color-brand))]", className)}>{STORE_NAME}</Link>;
}

const catHref = (c: string) => `/store?category=${encodeURIComponent(c)}#products`;

/**
 * The store's bar, the way a shop draws it: the shipping promise on a slim green strip, then the name, a wide search field and the basket, then the aisles as a row of links.
 * The basket opens as a panel (MiniCart) instead of leaving the page. On a phone the search and the aisles sit behind buttons. Sticky, so the basket is always one tap away.
 */
export function StoreHeader({ categories = [] }: { categories?: string[] }) {
  const { info, lines, ready, openCart } = useStore();
  const pathname = usePathname();
  const params = useSearchParams();
  const active = pathname === "/store" ? params?.get("category") ?? "" : null;
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

  const iconBtn = "flex h-11 w-11 items-center justify-center rounded-full text-foreground hover:bg-surface-muted";
  const navLink = (on: boolean) => cn("relative inline-flex h-12 items-center text-[15px] font-medium text-foreground/75 hover:text-foreground", on && "text-foreground after:absolute after:inset-x-0 after:bottom-0 after:h-[3px] after:rounded-full after:bg-[rgb(var(--color-brand))]");
  return (
    <header className="bg-background">
      {info?.shipping && (
        <div className="bg-[rgb(var(--color-brand))] text-[rgb(var(--color-background))]">
          <p className="mx-auto flex w-full max-w-[1240px] items-center justify-center gap-2 px-4 py-2 text-center text-[13px] sm:px-6"><Truck className="h-4 w-4 shrink-0" aria-hidden /> <span>{info.shipping}</span></p>
        </div>
      )}
      <div className="sticky top-0 z-40 bg-background">
      <div className="border-b border-[rgb(var(--color-border))]">
        <div className="mx-auto flex h-[68px] w-full max-w-[1240px] items-center gap-3 px-4 sm:px-6 lg:gap-8">
          <Wordmark />
          <form action="/store" method="get" role="search" className="hidden flex-1 md:block">
            <label className="relative block">
              <span className="sr-only">Search products</span>
              <Search className="pointer-events-none absolute left-4 top-1/2 h-[18px] w-[18px] -translate-y-1/2 text-muted" aria-hidden />
              <input type="search" name="q" placeholder={`Search ${STORE_NAME}`} className="h-11 w-full rounded-full bg-surface-muted pl-11 pr-5 text-[16px] text-foreground placeholder:text-muted focus-visible:bg-background focus-visible:ring-2 focus-visible:ring-[rgb(var(--color-brand))]" />
            </label>
          </form>
          <div className="ml-auto flex items-center gap-1 md:ml-0">
            <button type="button" className={cn(iconBtn, "md:hidden")} aria-label="Search" aria-expanded={panel === "search"} onClick={() => setPanel(panel === "search" ? null : "search")}>{panel === "search" ? <X className="h-5 w-5" aria-hidden /> : <Search className="h-5 w-5" aria-hidden />}</button>
            <button type="button" aria-label={`Basket, ${n} ${n === 1 ? "item" : "items"}`} onClick={openCart} className="relative flex h-11 items-center gap-2 rounded-full bg-[rgb(var(--color-brand))] px-4 text-[15px] font-medium text-[rgb(var(--color-background))] hover:brightness-110">
              <ShoppingBasket className="h-5 w-5" aria-hidden />
              <span className="hidden sm:inline" aria-hidden>Basket</span>
              {n > 0 && <span key={n} aria-hidden className="cr-bump flex h-[22px] min-w-[22px] items-center justify-center rounded-full bg-accent px-1.5 text-[12px] font-bold text-accent-foreground">{n}</span>}
            </button>
            <button type="button" className={cn(iconBtn, "md:hidden")} aria-label="Menu" aria-expanded={panel === "menu"} onClick={() => setPanel(panel === "menu" ? null : "menu")}>{panel === "menu" ? <X className="h-5 w-5" aria-hidden /> : <Menu className="h-5 w-5" aria-hidden />}</button>
          </div>
        </div>
      </div>
      <nav aria-label="Store" className="hidden border-b border-[rgb(var(--color-border))] md:block">
        <div className="mx-auto flex w-full max-w-[1240px] items-center gap-7 overflow-x-auto px-6">
          <Link href="/store#products" aria-current={active === "" ? "page" : undefined} className={navLink(active === "")}>All products</Link>
          {categories.map((c) => <Link key={c} href={catHref(c)} aria-current={active === c ? "page" : undefined} className={navLink(active === c)}>{c}</Link>)}
        </div>
      </nav>
      </div>

      {panel === "search" && (
        <div className="cr-fade border-b border-[rgb(var(--color-border))] bg-background md:hidden">
          <form action="/store" method="get" role="search" className="mx-auto flex w-full max-w-[1240px] items-center gap-3 px-4 py-3 sm:px-6">
            <Search className="h-5 w-5 shrink-0 text-muted" aria-hidden />
            <input ref={searchRef} type="search" name="q" aria-label="Search products" placeholder={`Search ${STORE_NAME}`} className="h-11 w-full rounded-full bg-surface-muted px-5 text-[17px] text-foreground placeholder:text-muted focus-visible:ring-2 focus-visible:ring-[rgb(var(--color-brand))]" />
          </form>
        </div>
      )}
      {panel === "menu" && (
        <nav aria-label="Menu" className="cr-fade border-b border-[rgb(var(--color-border))] bg-background md:hidden">
          <div className="mx-auto flex max-w-[1240px] flex-col px-4 py-3 sm:px-6">
            <Link href="/store#products" onClick={() => setPanel(null)} className="py-3 text-[22px] font-semibold tracking-[-0.03em]">All products</Link>
            {categories.map((c) => <Link key={c} href={catHref(c)} onClick={() => setPanel(null)} className="py-3 text-[22px] font-semibold tracking-[-0.03em] text-foreground/80">{c}</Link>)}
          </div>
        </nav>
      )}
      <MiniCart />
    </header>
  );
}
