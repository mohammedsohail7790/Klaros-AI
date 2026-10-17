import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { AddToCart } from "@/components/store/AddToCart";
import { StoreClosed, StoreUnavailable } from "@/components/store/Footer";
import { StoreProduct, getStoreInfo, getStoreProduct } from "@/lib/storeApi";

type Params = { params: Promise<{ slug: string }> };

async function load(slug: string): Promise<{ product: StoreProduct | null; closed: boolean; failed: boolean }> {
  try {
    if (!(await getStoreInfo())) return { product: null, closed: true, failed: false };
    return { product: await getStoreProduct(slug), closed: false, failed: false };
  } catch {
    return { product: null, closed: false, failed: true };
  }
}

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const { product } = await load((await params).slug);
  if (!product) return { title: "Product" };
  return { title: product.name, description: product.description?.slice(0, 160) ?? `Buy ${product.name} online.` };
}

/** One product, rendered on the server. Prices and stock may be up to a minute old here; the basket and checkout re-read them live. */
export default async function ProductPage({ params }: Params) {
  const { product, closed, failed } = await load((await params).slug);
  if (closed) return <StoreClosed />;
  if (failed) return <StoreUnavailable />;
  if (!product) notFound();
  return (
    <div className="grid gap-8 md:grid-cols-2">
      <div className="flex aspect-square items-center justify-center overflow-hidden rounded-2xl bg-surface-muted">
        {product.image_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={product.image_url} alt={product.name} className="h-full w-full object-cover" />
        ) : (
          <span aria-hidden className="text-7xl text-muted">{product.name.slice(0, 1)}</span>
        )}
      </div>
      <div className="space-y-4">
        <Link href="/store" className="text-sm text-muted hover:text-foreground">← All products</Link>
        {product.category && <p className="text-sm text-muted">{product.category}</p>}
        <h1 className="font-display text-3xl font-semibold tracking-tight text-foreground">{product.name}</h1>
        {product.description && <p className="whitespace-pre-line text-[15px] leading-relaxed text-foreground">{product.description}</p>}
        <AddToCart product={product} />
      </div>
    </div>
  );
}
