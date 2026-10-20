import Link from "next/link";
import { ProductVisual } from "@/components/store/LabelTile";
import { QuickAdd } from "@/components/store/QuickAdd";
import { buttonClass } from "@/components/store/styles";
import { formatMoney } from "@/lib/storeCart";
import type { StoreProduct } from "@/lib/storeApi";

/** Cheapest item price, and whether anything is in stock. */
export function productSummary(p: StoreProduct) {
  const prices = p.items.map((i) => Number(i.price)).filter((n) => !Number.isNaN(n));
  const cheapest = p.items.find((i) => Number(i.price) === Math.min(...prices)) ?? p.items[0];
  return { price: cheapest ? formatMoney(cheapest.price, cheapest.currency) : "", several: p.items.length > 1, inStock: p.items.some((i) => i.in_stock) };
}

/** A flat sage tile, centred: the picture, the name, the price, and the one action that makes sense (add, or choose when there are options). */
export function ProductCard({ product }: { product: StoreProduct }) {
  const s = productSummary(product);
  const single = product.items.length === 1 ? product.items[0] : null;
  return (
    <li className="flex">
      <div className="flex w-full flex-col rounded-[28px] bg-surface-muted p-4 text-center sm:p-5">
        <Link href={`/store/products/${product.slug}`} aria-hidden tabIndex={-1} className={`block ${s.inStock ? "" : "opacity-50"}`}>
          <ProductVisual name={product.name} category={product.category} imageUrl={product.image_url} tone="on-sage" />
        </Link>
        <div className="flex flex-1 flex-col items-center px-1 pb-1 pt-5">
          {product.category && <p className="text-[13px] text-muted">{product.category}</p>}
          <h3 className="mt-0.5 text-[19px] font-semibold leading-snug tracking-[-0.02em] text-foreground"><Link href={`/store/products/${product.slug}`} className="hover:underline">{product.name}</Link></h3>
          <p className="mt-1.5 text-[17px] text-foreground">{s.several && <span className="mr-1 text-muted">From</span>}{s.price}</p>
          <div className="mt-auto pt-4">
            {!s.inStock ? <span className="text-[15px] text-muted">Sold out</span>
              : single ? <QuickAdd sku={single.sku} slug={product.slug} name={product.name} />
              : <Link href={`/store/products/${product.slug}`} className={buttonClass("secondary", "sm")}>Choose</Link>}
          </div>
        </div>
      </div>
    </li>
  );
}
