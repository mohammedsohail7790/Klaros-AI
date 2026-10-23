import type { StoreProduct } from "@/lib/storeApi";
import { STORE_URL } from "@/lib/storeConfig";

/** The hosts that show the store (the same list `proxy.ts` uses; set STORE_HOSTS, comma separated, to change both). */
export function storeHosts(): string[] {
  return (process.env.STORE_HOSTS ?? "yourcleanroom.com,www.yourcleanroom.com").split(",").map((h) => h.trim().toLowerCase()).filter(Boolean);
}

export function isStoreHost(host: string | null | undefined): boolean {
  if (!host) return false;
  return storeHosts().includes(host.replace(/:\d+$/, "").toLowerCase());
}

export const productUrl = (slug: string) => `${STORE_URL}/store/products/${encodeURIComponent(slug)}`;

/**
 * schema.org Product data for search engines: only facts the shop holds (name, description, picture, price, stock). No ratings or reviews are ever added: the shop has none.
 * An item that is out of stock says so, so a search result never promises what cannot be bought.
 */
export function productJsonLd(product: StoreProduct): Record<string, unknown> {
  const url = productUrl(product.slug);
  return {
    "@context": "https://schema.org",
    "@type": "Product",
    name: product.name,
    ...(product.description ? { description: product.description.slice(0, 500) } : {}),
    ...(product.image_url ? { image: product.image_url } : {}),
    ...(product.category ? { category: product.category } : {}),
    url,
    offers: product.items.map((it) => ({
      "@type": "Offer",
      sku: it.sku,
      price: it.price,
      priceCurrency: it.currency,
      availability: it.in_stock ? "https://schema.org/InStock" : "https://schema.org/OutOfStock",
      url,
    })),
  };
}

/** JSON for an inline <script type="application/ld+json">: "<" is escaped so a product name can never close the tag. */
export const jsonLdScript = (data: unknown) => JSON.stringify(data).replace(/</g, "\\u003c");
