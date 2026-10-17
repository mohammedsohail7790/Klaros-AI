import Link from "next/link";
import { StoreClosed, StoreUnavailable } from "@/components/store/Footer";
import { ProductCard } from "@/components/store/parts";
import { StoreProduct, getStoreInfo, listStoreProducts } from "@/lib/storeApi";

const PAGE_SIZE = 24;

/** The shop front, rendered on the server so search engines can read it. Prices and stock may be up to a minute old here; the basket re-reads them live. */
export default async function StoreHome({ searchParams }: { searchParams: Promise<{ q?: string; category?: string; page?: string }> }) {
  const sp = await searchParams;
  const q = (sp.q ?? "").trim().slice(0, 80);
  const category = (sp.category ?? "").trim().slice(0, 120);
  const page = Math.max(1, Math.floor(Number(sp.page)) || 1);

  let products: StoreProduct[] = [];
  let categories: string[] = [];
  try {
    if (!(await getStoreInfo())) return <StoreClosed />;
    products = await listStoreProducts({ q, category, limit: PAGE_SIZE + 1, offset: (page - 1) * PAGE_SIZE });
    const all = await listStoreProducts({ limit: 100 });
    categories = [...new Set(all.map((p) => p.category).filter((c): c is string => !!c))].sort();
  } catch {
    return <StoreUnavailable />;
  }
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

  return (
    <div className="space-y-6">
      <form action="/store" method="get" role="search" className="flex flex-wrap items-center gap-3">
        <input type="search" name="q" defaultValue={q} aria-label="Search products" placeholder="Search products" className="h-11 w-full max-w-sm rounded-full bg-surface-muted px-5" />
        {category && <input type="hidden" name="category" value={category} />}
        <button type="submit" className="h-11 rounded-full bg-accent px-6 text-sm font-medium text-accent-foreground">Search</button>
      </form>
      {categories.length > 0 && (
        <nav aria-label="Categories" className="flex flex-wrap gap-2 text-sm">
          <Link href={link({ category: "" })} className={`rounded-full px-4 py-2 ${!category ? "bg-accent text-accent-foreground" : "bg-surface-muted"}`}>All</Link>
          {categories.map((c) => <Link key={c} href={link({ category: c })} className={`rounded-full px-4 py-2 ${category === c ? "bg-accent text-accent-foreground" : "bg-surface-muted"}`}>{c}</Link>)}
        </nav>
      )}
      {shown.length === 0 ? (
        <p className="py-12 text-center text-muted">{q || category ? "Nothing matches your search." : "There are no products on sale yet."}</p>
      ) : (
        <ul className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">{shown.map((p) => <ProductCard key={p.slug} product={p} />)}</ul>
      )}
      {(page > 1 || hasNext) && (
        <nav aria-label="Pages" className="flex justify-between text-sm">
          {page > 1 ? <Link href={link({ page: page - 1 })} rel="prev">← Previous</Link> : <span />}
          {hasNext && <Link href={link({ page: page + 1 })} rel="next">Next →</Link>}
        </nav>
      )}
    </div>
  );
}
