import Link from "next/link";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { StoreClosed, StoreUnavailable, TrustStrip } from "@/components/store/Footer";
import { ProductVisual } from "@/components/store/LabelTile";
import { ProductCard, productSummary } from "@/components/store/parts";
import { buttonClass, linkClass } from "@/components/store/styles";
import { Chip } from "@/components/store/ui";
import { cn } from "@/lib/cn";
import { StoreProduct, getStoreInfo, listStoreProducts } from "@/lib/storeApi";

const PAGE_SIZE = 24;
// Category tiles alternate between the palette's light and dark surfaces, the way apple.com alternates its promo tiles.
const TILES = [
  { bg: "bg-surface-muted text-foreground", sub: "text-muted", dark: false },
  { bg: "bg-[rgb(var(--color-brand))] text-[rgb(var(--color-background))]", sub: "text-[rgb(var(--color-background))]/80", dark: true },
  { bg: "bg-[rgb(var(--color-foreground))] text-[rgb(var(--color-background))]", sub: "text-[rgb(var(--color-background))]/70", dark: true },
];

/** The shop front, rendered on the server so search engines can read it. Prices and stock may be up to a minute old here; the basket re-reads them live. */
export default async function StoreHome({ searchParams }: { searchParams: Promise<{ q?: string; category?: string; page?: string }> }) {
  const sp = await searchParams;
  const q = (sp.q ?? "").trim().slice(0, 80);
  const category = (sp.category ?? "").trim().slice(0, 120);
  const page = Math.max(1, Math.floor(Number(sp.page)) || 1);

  let info;
  let products: StoreProduct[] = [];
  let all: StoreProduct[] = [];
  try {
    info = await getStoreInfo();
    if (!info) return <StoreClosed />;
    products = await listStoreProducts({ q, category, limit: PAGE_SIZE + 1, offset: (page - 1) * PAGE_SIZE });
    all = await listStoreProducts({ limit: 100 });
  } catch {
    return <StoreUnavailable />;
  }
  const counts = new Map<string, number>();
  for (const p of all) if (p.category) counts.set(p.category, (counts.get(p.category) ?? 0) + 1);
  const categories = [...counts.keys()].sort();
  const featured = all.filter((p) => productSummary(p).inStock).slice(0, 3);
  const isHome = !q && !category && page === 1;
  const hasNext = products.length > PAGE_SIZE;
  const shown = products.slice(0, PAGE_SIZE);
  const link = (over: { page?: number; category?: string }) => {
    const qs = new URLSearchParams();
    if (q) qs.set("q", q);
    const c = over.category !== undefined ? over.category : category;
    if (c) qs.set("category", c);
    if (over.page && over.page > 1) qs.set("page", String(over.page));
    const s = qs.toString();
    return s ? `/store?${s}` : "/store";
  };
  const heading = q ? `Results for “${q}”` : category || "All products";

  return (
    <div className="space-y-20 sm:space-y-28">
      {isHome && (
        <section aria-label="Welcome" className="cr-on-dark overflow-hidden rounded-[32px] bg-[rgb(var(--color-brand))] text-[rgb(var(--color-background))]">
          <div className="px-6 pb-12 pt-14 text-center sm:px-12 sm:pt-20">
            <h1 className="mx-auto max-w-3xl text-[44px] font-semibold leading-[1.04] sm:text-[72px]">Pantry staples, delivered.</h1>
            <p className="mx-auto mt-5 max-w-xl text-[19px] leading-snug text-[rgb(var(--color-background))]/85 sm:text-[24px]">Shelf-stable groceries shipped straight to you, anywhere in the continental United States.</p>
            <div className="mt-8 flex flex-wrap items-center justify-center gap-x-6 gap-y-4">
              <a href="#products" className={buttonClass("primary", "lg")}>Shop all products</a>
              {categories.length > 0 && <a href="#categories" className={linkClass(true)}>Browse categories <ChevronRight className="h-5 w-5" aria-hidden /></a>}
            </div>
            {info.free_shipping_over && Number(info.free_shipping_over) > 0 && <p className="mt-6"><Chip tone="apricot" className="px-4 py-1.5 text-[14px] font-semibold">Free shipping from ${info.free_shipping_over}</Chip></p>}
          </div>
          {featured.length > 0 && (
            <ul className="mx-auto grid max-w-2xl grid-cols-3 gap-3 px-6 pb-0 sm:gap-5 sm:px-12" aria-label="In stock now">
              {featured.map((p, i) => (
                <li key={p.slug} className={cn(i === 1 ? "translate-y-4 sm:translate-y-6" : "translate-y-8 sm:translate-y-12")}>
                  <Link href={`/store/products/${p.slug}`} className="block">
                    <ProductVisual name={p.name} imageUrl={p.image_url} size="mini" tone="on-sage" />
                    <span className="sr-only">{p.name}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
          {featured.length > 0 && <div className="h-10 sm:h-14" />}
        </section>
      )}

      {isHome && <TrustStrip />}

      {isHome && categories.length > 0 && (
        <section id="categories" aria-label="Shop by category" className="scroll-mt-20">
          <h2 className="mb-8 text-[32px] sm:text-[40px]">Shop by category</h2>
          <ul className="grid gap-3 sm:grid-cols-2 sm:gap-4">
            {categories.map((c, i) => {
              const t = TILES[i % TILES.length];
              return (
                <li key={c} className="sm:[&:last-child:nth-child(odd)]:col-span-2">
                  <div className={cn("flex min-h-[260px] flex-col items-center justify-center rounded-[32px] px-6 py-12 text-center sm:min-h-[320px]", t.bg)}>
                    <h3 className="text-[32px] sm:text-[40px]" style={{ color: "inherit" }}>{c}</h3>
                    <p className={cn("mt-2 text-[19px]", t.sub)}>{counts.get(c)} {counts.get(c) === 1 ? "product" : "products"}</p>
                    <Link href={`${link({ category: c })}#products`} className={cn(linkClass(t.dark), "mt-5")} aria-label={`Shop ${c}`}>Shop <ChevronRight className="h-5 w-5" aria-hidden /></Link>
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      <section id="products" aria-label="Products" className="scroll-mt-20 space-y-8">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <h2 className="text-[32px] sm:text-[40px]">{heading}</h2>
          {(q || category) && <Link href="/store" className={linkClass()}>Clear search</Link>}
        </div>
        {categories.length > 0 && (
          <nav aria-label="Categories" className="-mx-4 flex gap-2 overflow-x-auto px-4 pb-1 sm:mx-0 sm:flex-wrap sm:px-0">
            {["", ...categories].map((c) => {
              const active = (c || "") === category;
              return <Link key={c || "all"} href={`${link({ category: c })}#products`} aria-current={active ? "page" : undefined} className={cn("shrink-0 rounded-full px-5 py-2 text-[15px] font-medium transition-colors", active ? "bg-[rgb(var(--color-foreground))] text-[rgb(var(--color-background))]" : "bg-surface-muted text-foreground hover:brightness-95")}>{c || "All"}</Link>;
            })}
          </nav>
        )}
        {shown.length === 0 ? (
          <div className="rounded-[28px] bg-surface-muted px-6 py-20 text-center">
            <p className="text-[24px] font-semibold tracking-[-0.02em]">{q || category ? "Nothing matches your search." : "There are no products on sale yet."}</p>
            {(q || category) && <p className="mt-2 text-muted">Try a different word, or <Link href="/store" className="text-[rgb(var(--color-brand))] underline underline-offset-4">browse everything</Link>.</p>}
          </div>
        ) : (
          <ul className="grid grid-cols-2 gap-3 sm:gap-4 md:grid-cols-3">{shown.map((p) => <ProductCard key={p.slug} product={p} />)}</ul>
        )}
        {(page > 1 || hasNext) && (
          <nav aria-label="Pages" className="flex items-center justify-between pt-2">
            {page > 1 ? <Link href={`${link({ page: page - 1 })}#products`} rel="prev" className={buttonClass("secondary", "md")}><ChevronLeft className="h-4 w-4" aria-hidden /> Previous page</Link> : <span />}
            {hasNext && <Link href={`${link({ page: page + 1 })}#products`} rel="next" className={buttonClass("secondary", "md")}>Next page <ChevronRight className="h-4 w-4" aria-hidden /></Link>}
          </nav>
        )}
      </section>
    </div>
  );
}
