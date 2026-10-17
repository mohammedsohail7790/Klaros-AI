import Link from "next/link";
import { formatMoney } from "@/lib/storeCart";
import type { StoreProduct } from "@/lib/storeApi";

/** Cheapest item price, and whether anything is in stock. */
export function productSummary(p: StoreProduct) {
  const prices = p.items.map((i) => Number(i.price)).filter((n) => !Number.isNaN(n));
  const cheapest = p.items.find((i) => Number(i.price) === Math.min(...prices)) ?? p.items[0];
  return { price: cheapest ? formatMoney(cheapest.price, cheapest.currency) : "", several: p.items.length > 1, inStock: p.items.some((i) => i.in_stock) };
}

export function ProductCard({ product }: { product: StoreProduct }) {
  const s = productSummary(product);
  return (
    <li className="rounded-2xl border border-border bg-surface p-4">
      <Link href={`/store/products/${product.slug}`} className="block">
        <div className="mb-3 flex aspect-square items-center justify-center overflow-hidden rounded-xl bg-surface-muted">
          {product.image_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={product.image_url} alt={product.name} loading="lazy" className="h-full w-full object-cover" />
          ) : (
            <span aria-hidden className="text-4xl text-muted">{product.name.slice(0, 1)}</span>
          )}
        </div>
        <h2 className="text-[15px] font-medium text-foreground">{product.name}</h2>
      </Link>
      <p className="mt-1 text-sm text-muted">{s.several && "From "}{s.price}</p>
      {!s.inStock && <p className="mt-1 text-sm text-danger">Out of stock</p>}
    </li>
  );
}
