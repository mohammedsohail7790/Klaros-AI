import Link from "next/link";
import { ProductVisual } from "@/components/store/LabelTile";
import { QuickAdd } from "@/components/store/QuickAdd";
import { buttonClass } from "@/components/store/styles";
import { cn } from "@/lib/cn";
import { formatMoney } from "@/lib/storeCart";
import type { StoreProduct } from "@/lib/storeApi";

/** Cheapest item price, and whether anything is in stock. */
export function productSummary(p: StoreProduct) {
  const prices = p.items.map((i) => Number(i.price)).filter((n) => !Number.isNaN(n));
  const cheapest = p.items.find((i) => Number(i.price) === Math.min(...prices)) ?? p.items[0];
  return { price: cheapest ? formatMoney(cheapest.price, cheapest.currency) : "", several: p.items.length > 1, inStock: p.items.some((i) => i.in_stock) };
}

/**
 * A product in a grid or a rail: the picture on a sage tile, the aisle, the name, the price and the one action that makes sense (add, or choose when there are options).
 * A sold-out item is greyed and badged; a product marked non-perishable carries a quiet "Shelf-stable" tag, which is true of everything the store lists.
 */
export function ProductCard({ product, className }: { product: StoreProduct; className?: string }) {
  const s = productSummary(product);
  const single = product.items.length === 1 ? product.items[0] : null;
  const href = `/store/products/${product.slug}`;
  return (
    <li className={cn("flex", className)}>
      <article className="group relative flex w-full flex-col overflow-hidden rounded-[20px] border border-[rgb(var(--color-border))] bg-background transition-[border-color,box-shadow] duration-200 hover:border-[rgb(var(--color-foreground))]/35 hover:shadow-[0_10px_30px_-12px_rgba(26,32,28,0.25)]">
        <Link href={href} aria-hidden tabIndex={-1} className={cn("relative block p-2.5 pb-0", !s.inStock && "opacity-55")}>
          <ProductVisual name={product.name} category={product.category} imageUrl={product.image_url} tone="on-cream" />
        </Link>
        <div className="pointer-events-none absolute left-4 top-4 flex flex-col items-start gap-1.5">
          {!s.inStock ? <span className="rounded-full bg-foreground px-3 py-1 text-[12px] font-medium text-background">Sold out</span>
            : product.non_perishable ? <span className="rounded-full bg-background/90 px-3 py-1 text-[12px] font-medium text-[rgb(var(--color-brand))] backdrop-blur">Shelf-stable</span> : null}
        </div>
        <div className="flex flex-1 flex-col p-4 pt-3.5">
          {product.category && <p className="text-[13px] text-muted">{product.category}</p>}
          <h3 className="mt-0.5 line-clamp-2 text-[17px] font-semibold leading-snug tracking-[-0.015em] text-foreground"><Link href={href} className="after:absolute after:inset-0 hover:underline">{product.name}</Link></h3>
          <p className="mt-2 text-[20px] font-semibold tracking-[-0.02em] text-foreground">{s.several && <span className="mr-1 text-[14px] font-normal text-muted">From</span>}{s.price}</p>
          <div className="relative mt-auto pt-4">
            {!s.inStock ? <span className="flex h-9 items-center text-[15px] text-muted">Currently unavailable</span>
              : single ? <QuickAdd sku={single.sku} slug={product.slug} name={product.name} className="w-full" />
              : <Link href={href} className={buttonClass("secondary", "sm", "w-full")}>Choose</Link>}
          </div>
        </div>
      </article>
    </li>
  );
}

/** Products in a row that scrolls sideways and snaps card by card; on a wide screen four fit at once. */
export function ProductRail({ products, label }: { products: StoreProduct[]; label: string }) {
  return (
    <ul aria-label={label} className="-mx-4 flex snap-x snap-mandatory gap-4 overflow-x-auto px-4 pb-4 sm:-mx-6 sm:px-6 [scrollbar-width:thin]">
      {products.map((p) => <ProductCard key={p.slug} product={p} className="w-[220px] shrink-0 snap-start sm:w-[250px]" />)}
    </ul>
  );
}
