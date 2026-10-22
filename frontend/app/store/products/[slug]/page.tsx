import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ChevronRight, ReceiptText, ShieldCheck, Truck } from "lucide-react";
import { AddToCart } from "@/components/store/AddToCart";
import { StoreClosed, StoreUnavailable } from "@/components/store/Footer";
import { ProductVisual } from "@/components/store/LabelTile";
import { ProductCard } from "@/components/store/parts";
import { StoreInfo, StoreProduct, getStoreInfo, getStoreProduct, listStoreProducts } from "@/lib/storeApi";
import { STORE_NAME } from "@/lib/storeConfig";
import { jsonLdScript, productJsonLd, productUrl } from "@/lib/storeSeo";

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
  const description = product.description?.slice(0, 160) ?? `Buy ${product.name} online.`;
  return {
    title: product.name, description, alternates: { canonical: productUrl(product.slug) },
    openGraph: { type: "website", siteName: STORE_NAME, title: product.name, description, url: productUrl(product.slug), ...(product.image_url ? { images: [product.image_url] } : {}) },
  };
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
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: jsonLdScript(productJsonLd(product)) }} />
      <div>
        <nav aria-label="Breadcrumb" className="mb-8 flex flex-wrap items-center gap-1.5 text-[13px] text-muted">
          <Link href="/store" className="hover:text-foreground hover:underline">Store</Link>
          {product.category && <><ChevronRight className="h-3.5 w-3.5" aria-hidden /><Link href={`/store?category=${encodeURIComponent(product.category)}`} className="hover:text-foreground hover:underline">{product.category}</Link></>}
          <ChevronRight className="h-3.5 w-3.5" aria-hidden /><span aria-current="page" className="text-foreground">{product.name}</span>
        </nav>
        <div className="grid gap-8 lg:grid-cols-[1.15fr_1fr] lg:gap-14">
          <div className="self-start">
            <ProductVisual name={product.name} category={product.category} imageUrl={product.image_url} size="large" priority />
          </div>
          <div className="space-y-7 self-start rounded-[28px] border border-[rgb(var(--color-border))] p-6 sm:p-9 lg:sticky lg:top-40">
            <div>
              <div className="mb-3 flex flex-wrap items-center gap-2">
                {product.category && <Link href={`/store?category=${encodeURIComponent(product.category)}#products`} className="rounded-full bg-surface-muted px-3 py-1 text-[13px] font-medium hover:brightness-95">{product.category}</Link>}
                {product.non_perishable && <span className="rounded-full bg-[rgb(var(--color-brand))] px-3 py-1 text-[13px] font-medium text-[rgb(var(--color-background))]">Shelf-stable</span>}
              </div>
              <h1 className="text-[34px] leading-[1.06] sm:text-[46px]">{product.name}</h1>
            </div>
            <AddToCart product={product} />
            <ul className="divide-y divide-[rgb(var(--color-border))] border-t border-[rgb(var(--color-border))]">
              {rows.map(({ icon: Icon, title, body }) => (
                <li key={title} className="flex gap-4 py-4">
                  <Icon className="mt-0.5 h-5 w-5 shrink-0 text-[rgb(var(--color-brand))]" strokeWidth={1.75} aria-hidden />
                  <div><p className="text-[15px] font-semibold text-foreground">{title}</p>{body && <p className="text-[14px] text-muted">{body}</p>}</div>
                </li>
              ))}
            </ul>
          </div>
        </div>
        {product.description && (
          <section aria-label="About this product" className="mt-14 max-w-[68ch] border-t border-[rgb(var(--color-border))] pt-10">
            <h2 className="text-[26px] sm:text-[32px]">About this product</h2>
            <p className="mt-4 whitespace-pre-line text-[18px] leading-relaxed text-foreground/90">{product.description}</p>
          </section>
        )}
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
