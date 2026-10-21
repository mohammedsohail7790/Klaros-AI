import Link from "next/link";
import { Check, ChevronLeft, ChevronRight, Truck, X } from "lucide-react";
import { FilterToggle } from "@/components/store/Filters";
import { StoreClosed, StoreUnavailable, TrustStrip } from "@/components/store/Footer";
import { ShelfMark } from "@/components/store/Illustrations";
import { ProductCard, ProductRail, productSummary } from "@/components/store/parts";
import { buttonClass, linkClass } from "@/components/store/styles";
import { cn } from "@/lib/cn";
import { STORE_NAME } from "@/lib/storeConfig";
import { StoreInfo, StoreProduct, getStoreInfo, listAllStoreProducts } from "@/lib/storeApi";
import { Filters, PRICE_BANDS, SORTS, aisleCounts, filterHref, filterProducts, isDefault, paginate, parseFilters, sortProducts } from "@/lib/storeCatalog";

/** The shop. Rendered on the server so search engines can read it; prices and stock may be up to a minute old here and the basket re-reads them live. */
export default async function StoreHome({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const f = parseFilters(await searchParams);
  let info: StoreInfo | null;
  let all: StoreProduct[] = [];
  try {
    info = await getStoreInfo();
    if (!info) return <StoreClosed />;
    all = await listAllStoreProducts();
  } catch {
    return <StoreUnavailable />;
  }

  const aisles = [...new Set(all.map((p) => p.category).filter((c): c is string => !!c))].sort();
  const counts = aisleCounts(all, f);
  const matching = sortProducts(filterProducts(all, f), f.sort);
  const { items, hasNext } = paginate(matching, f.page);
  const home = isDefault(f);
  const inStockNow = all.filter((p) => productSummary(p).inStock);
  const activeFilters = [f.category && "category", f.stock && "stock", f.price && "price"].filter(Boolean).length;
  const heading = f.q ? `Results for “${f.q}”` : f.category || "All products";

  return (
    <div className="space-y-16 sm:space-y-24">
      {home && <Promo info={info} />}

      {home && aisles.length > 0 && (
        <section aria-label="Shop by category" className="scroll-mt-40">
          <div className="mb-6 flex items-end justify-between gap-4">
            <h2 className="text-[28px] sm:text-[36px]">Shop by aisle</h2>
          </div>
          <ul className="-mx-4 flex gap-4 overflow-x-auto px-4 pb-3 sm:-mx-6 sm:gap-6 sm:px-6 [scrollbar-width:thin]">
            {aisles.map((c) => (
              <li key={c} className="shrink-0">
                <Link href={filterHref(f, { category: c })} aria-label={`Shop ${c}`} className="group flex w-[104px] flex-col items-center gap-3 text-center sm:w-[124px]">
                  <span aria-hidden className="flex h-[88px] w-[88px] items-center justify-center rounded-full bg-surface-muted text-[34px] font-semibold tracking-[-0.04em] text-[rgb(var(--color-brand))] transition-transform duration-200 group-hover:scale-105 sm:h-[104px] sm:w-[104px] sm:text-[40px]">{c.slice(0, 1).toUpperCase()}</span>
                  <span className="text-[15px] font-medium leading-tight text-foreground group-hover:underline">{c}</span>
                  <span className="-mt-2 text-[13px] text-muted">{counts.get(c) ?? 0} {counts.get(c) === 1 ? "product" : "products"}</span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      {home && inStockNow.length > 0 && (
        <section aria-label="In stock now">
          <div className="mb-6 flex items-end justify-between gap-4">
            <h2 className="text-[28px] sm:text-[36px]">In stock now</h2>
            <a href="#products" className={linkClass()}>See everything <ChevronRight className="h-5 w-5" aria-hidden /></a>
          </div>
          <ProductRail products={inStockNow.slice(0, 12)} label="In stock now" />
        </section>
      )}

      <section id="products" aria-label="Products" className="scroll-mt-40 space-y-6">
        <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
          <div>
            <h2 className="text-[28px] sm:text-[36px]">{heading}</h2>
            <p className="mt-1 text-[15px] text-muted" aria-live="polite">{matching.length} {matching.length === 1 ? "product" : "products"}</p>
          </div>
          <nav aria-label="Sort" className="-mx-4 flex gap-2 overflow-x-auto px-4 sm:mx-0 sm:px-0">
            {SORTS.map((s) => {
              const on = f.sort === s.id;
              return <Link key={s.id} href={filterHref(f, { sort: s.id })} aria-current={on ? "true" : undefined} className={cn("shrink-0 rounded-full px-4 py-2 text-[14px] font-medium transition-colors", on ? "bg-foreground text-background" : "bg-surface-muted text-foreground hover:brightness-95")}>{s.label}</Link>;
            })}
          </nav>
        </div>

        <div className="grid gap-8 lg:grid-cols-[236px_1fr] lg:gap-12">
          <FilterToggle activeCount={activeFilters}>
            <FilterGroup title="Aisle">
              <Option href={filterHref(f, { category: "" })} on={!f.category}>All aisles <small>{[...aisleCounts(all, { ...f, category: "" }).values()].reduce((a, b) => a + b, 0)}</small></Option>
              {aisles.map((c) => <Option key={c} href={filterHref(f, { category: c })} on={f.category === c}>{c} <small>{counts.get(c) ?? 0}</small></Option>)}
            </FilterGroup>
            <FilterGroup title="Availability">
              <Option href={filterHref(f, { stock: !f.stock })} on={f.stock} check>In stock only</Option>
            </FilterGroup>
            <FilterGroup title="Price">
              <Option href={filterHref(f, { price: "" })} on={!f.price}>Any price</Option>
              {PRICE_BANDS.map((b) => <Option key={b.id} href={filterHref(f, { price: b.id })} on={f.price === b.id}>{b.label}</Option>)}
            </FilterGroup>
            {!isDefault({ ...f, sort: "featured", page: 1 }) && <Link href="/store#products" className={linkClass()}>Clear all filters</Link>}
          </FilterToggle>

          <div>
            <Applied f={f} />
            {items.length === 0 ? (
              <div className="rounded-[24px] bg-surface-muted px-6 py-20 text-center">
                <p className="text-[24px] font-semibold tracking-[-0.02em]">{f.q || activeFilters ? "Nothing matches that." : "Nothing here yet."}</p>
                <p className="mt-2 text-[17px] text-muted">{f.q || activeFilters ? "Try a different word or remove a filter." : "Please check back soon."}</p>
                {(f.q || activeFilters > 0) && <p className="mt-7"><Link href="/store#products" className={buttonClass("secondary", "md")}>Clear search and filters</Link></p>}
              </div>
            ) : (
              <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 sm:gap-4 xl:grid-cols-4">{items.map((p) => <ProductCard key={p.slug} product={p} />)}</ul>
            )}
            {(f.page > 1 || hasNext) && (
              <nav aria-label="Pages" className="mt-12 flex items-center justify-center gap-4">
                {f.page > 1 && <Link href={filterHref(f, { page: f.page - 1 })} className={buttonClass("secondary", "md")}><ChevronLeft className="h-4 w-4" aria-hidden /> Previous page</Link>}
                <span className="text-[15px] text-muted">Page {f.page}</span>
                {hasNext && <Link href={filterHref(f, { page: f.page + 1 })} className={buttonClass("secondary", "md")}>Next page <ChevronRight className="h-4 w-4" aria-hidden /></Link>}
              </nav>
            )}
          </div>
        </div>
      </section>

      {home && <TrustStrip />}
    </div>
  );
}

/** The top of the shop: one large offer and two small ones, all drawn from the store's real rules. */
function Promo({ info }: { info: StoreInfo }) {
  const shipping = info.free_shipping_over && Number(info.free_shipping_over) > 0 ? { big: `Free shipping from $${info.free_shipping_over}`, small: info.shipping } : { big: "Delivered across the continental US", small: info.shipping };
  return (
    <section aria-label="Welcome" className="grid gap-4 lg:grid-cols-[1.9fr_1fr]">
      <div className="cr-on-dark relative flex min-h-[340px] flex-col justify-between overflow-hidden rounded-[28px] bg-[rgb(var(--color-brand))] p-7 text-[rgb(var(--color-background))] sm:min-h-[400px] sm:p-10">
        <div className="relative z-10 max-w-[19ch] sm:max-w-[54%]">
          <h1 className="text-[40px] font-semibold leading-[1.02] tracking-[-0.04em] sm:text-[60px]">Pantry staples, delivered.</h1>
          <p className="mt-4 max-w-[30ch] text-[17px] leading-snug text-[rgb(var(--color-background))]/85 sm:text-[20px]">Shelf-stable groceries shipped to your door across the continental United States.</p>
        </div>
        <div className="relative z-10 mt-8 flex flex-wrap items-center gap-x-6 gap-y-3">
          <a href="#products" className={buttonClass("primary", "lg")}>Shop all products</a>
          <Link href="/store/welcome" className={linkClass(true)}>About {STORE_NAME} <ChevronRight className="h-5 w-5" aria-hidden /></Link>
        </div>
        <ShelfMark className="pointer-events-none absolute -bottom-4 -right-6 hidden h-auto w-[44%] max-w-[470px] sm:block" />
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-1">
        <div className="flex min-h-[180px] flex-col justify-between rounded-[28px] bg-surface-muted p-6 sm:p-7">
          <Truck className="h-8 w-8 text-[rgb(var(--color-brand))]" strokeWidth={1.5} aria-hidden />
          <div><p className="text-[24px] font-semibold leading-tight tracking-[-0.03em] text-foreground">{shipping.big}</p>{shipping.small && <p className="mt-1.5 text-[14px] text-muted">{shipping.small}</p>}</div>
        </div>
        <div className="flex min-h-[180px] flex-col justify-between rounded-[28px] bg-accent p-6 text-accent-foreground sm:p-7">
          <Check className="h-8 w-8" strokeWidth={2} aria-hidden />
          <div><p className="text-[24px] font-semibold leading-tight tracking-[-0.03em]">No fridge. No freezer.</p><p className="mt-1.5 text-[14px] opacity-85">Everything on our shelves is shelf-stable.</p></div>
        </div>
      </div>
    </section>
  );
}

function FilterGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section aria-label={title}>
      <h3 className="mb-3 text-[15px] font-semibold tracking-[-0.01em] text-foreground">{title}</h3>
      <ul className="space-y-1">{children}</ul>
    </section>
  );
}

function Option({ href, on, check, children }: { href: string; on: boolean; check?: boolean; children: React.ReactNode }) {
  return (
    <li>
      <Link href={href} aria-current={on ? "true" : undefined} className={cn("flex items-center justify-between gap-3 rounded-xl px-3 py-2 text-[15px] transition-colors [&_small]:text-[13px] [&_small]:text-muted", on ? "bg-surface-muted font-semibold text-foreground" : "text-foreground/80 hover:bg-surface-muted/70")}>
        <span className="flex items-center gap-2">{check && <span aria-hidden className={cn("flex h-[18px] w-[18px] items-center justify-center rounded-[5px] border", on ? "border-[rgb(var(--color-brand))] bg-[rgb(var(--color-brand))] text-[rgb(var(--color-background))]" : "border-[rgb(var(--color-foreground))]/40")}>{on && <Check className="h-3 w-3" strokeWidth={3} />}</span>}{children}</span>
      </Link>
    </li>
  );
}

/** What is currently narrowing the list, each with a way to take it off. */
function Applied({ f }: { f: Filters }) {
  const chips: { label: string; href: string }[] = [];
  if (f.q) chips.push({ label: `“${f.q}”`, href: filterHref(f, { q: "" }) });
  if (f.category) chips.push({ label: f.category, href: filterHref(f, { category: "" }) });
  if (f.price) chips.push({ label: PRICE_BANDS.find((b) => b.id === f.price)!.label, href: filterHref(f, { price: "" }) });
  if (f.stock) chips.push({ label: "In stock only", href: filterHref(f, { stock: false }) });
  if (chips.length === 0) return null;
  return (
    <ul aria-label="Applied filters" className="mb-5 flex flex-wrap items-center gap-2">
      {chips.map((c) => <li key={c.label}><Link href={c.href} aria-label={`Remove ${c.label}`} className="inline-flex items-center gap-1.5 rounded-full bg-surface-muted py-1.5 pl-3.5 pr-2.5 text-[14px] font-medium hover:brightness-95">{c.label}<X className="h-3.5 w-3.5" aria-hidden /></Link></li>)}
    </ul>
  );
}
