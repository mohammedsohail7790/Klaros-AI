import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ChevronRight, ReceiptText, ShieldCheck, Truck } from "lucide-react";
import { AddToCart } from "@/components/store/AddToCart";
import { StoreClosed, StoreUnavailable } from "@/components/store/Footer";
import { ProductVisual } from "@/components/store/LabelTile";
import { ProductCard } from "@/components/store/parts";
import { StoreInfo, StoreProduct, getStoreInfo, getStoreProduct, listStoreProducts } from "@/lib/storeApi";

type Params = { params: Promise<{ slug: string }> };

async function load(slug: string): Promise<{ product: StoreProduct | null; info: StoreInfo | null; closed: boolean; failed: boolean }> {
  try {
    const info = await getStoreInfo();
    if (!info) return { product: null, info: null, closed: true, failed: false };
    return { product: await getStoreProduct(slug), info, closed: false, failed: false };
  } catch {
    return { product: null, info: null, closed: false, failed: true };
  }
}

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const { product } = await load((await params).slug);
  if (!product) return { title: "Product" };
  return { title: product.name, description: product.description?.slice(0, 160) ?? `Buy ${product.name} online.` };
}

/** One product, rendered on the server. Prices and stock may be up to a minute old here; the basket and checkout re-read them live. */
export default async function ProductPage({ params }: Params) {
  const { product, info, closed, failed } = await load((await params).slug);
  if (closed) return <StoreClosed />;
  if (failed) return <StoreUnavailable />;
  if (!product || !info) notFound();

  let related: StoreProduct[] = [];
  try {
    related = (await listStoreProducts({ category: product.category ?? undefined, limit: 5 })).filter((p) => p.slug !== product.slug).slice(0, 4);
  } catch {
    // the page is complete without suggestions
  }
  const rows = [
    { icon: Truck, title: "Delivered across the continental US", body: info.shipping },
    { icon: ReceiptText, title: "Sales tax", body: info.sales_tax_notice },
    { icon: ShieldCheck, title: "Secure card payment", body: "You pay on Stripe's own page. We never see your card." },
  ];

  return (
    <div className="space-y-24">
      <div>
        <nav aria-label="Breadcrumb" className="mb-8 flex flex-wrap items-center gap-1.5 text-[13px] text-muted">
          <Link href="/store" className="hover:text-foreground hover:underline">Store</Link>
          {product.category && <><ChevronRight className="h-3.5 w-3.5" aria-hidden /><Link href={`/store?category=${encodeURIComponent(product.category)}`} className="hover:text-foreground hover:underline">{product.category}</Link></>}
          <ChevronRight className="h-3.5 w-3.5" aria-hidden /><span aria-current="page" className="text-foreground">{product.name}</span>
        </nav>
        <div className="grid gap-10 lg:grid-cols-[1.1fr_1fr] lg:gap-20">
          <div className="self-start lg:sticky lg:top-24">
            <ProductVisual name={product.name} category={product.category} imageUrl={product.image_url} size="large" priority />
          </div>
          <div className="space-y-9">
            <div>
              {product.category && <p className="mb-2 text-[15px] text-muted">{product.category}</p>}
              <h1 className="text-[40px] leading-[1.05] sm:text-[56px]">{product.name}</h1>
            </div>
            <AddToCart product={product} />
            {product.description && <p className="max-w-prose whitespace-pre-line text-[19px] leading-relaxed text-foreground">{product.description}</p>}
            <ul className="divide-y divide-[rgb(var(--color-border))] border-y border-[rgb(var(--color-border))]">
              {rows.map(({ icon: Icon, title, body }) => (
                <li key={title} className="flex gap-4 py-5">
                  <Icon className="mt-0.5 h-6 w-6 shrink-0 text-[rgb(var(--color-brand))]" strokeWidth={1.5} aria-hidden />
                  <div><p className="text-[17px] font-semibold text-foreground">{title}</p>{body && <p className="text-[15px] text-muted">{body}</p>}</div>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>

      {related.length > 0 && (
        <section aria-label="More like this">
          <h2 className="mb-8 text-[32px] sm:text-[40px]">You may also like</h2>
          <ul className="grid grid-cols-2 gap-3 sm:gap-4 md:grid-cols-4">{related.map((p) => <ProductCard key={p.slug} product={p} />)}</ul>
        </section>
      )}
    </div>
  );
}
