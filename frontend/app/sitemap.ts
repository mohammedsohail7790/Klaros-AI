import type { MetadataRoute } from "next";
import { headers } from "next/headers";
import { SITE_URL } from "@/lib/site";
import { listAllStoreProducts } from "@/lib/storeApi";
import { STORE_URL } from "@/lib/storeConfig";
import { isStoreHost, productUrl } from "@/lib/storeSeo";

/**
 * On the store's own host: the welcome page, the shop and one page per product on sale (a closed shop lists only its two fixed pages, and an unreachable one never makes the
 * sitemap error). Every other host gets the Klaros marketing sitemap below, unchanged.
 */
export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  if (isStoreHost((await headers()).get("host"))) {
    const fixed: MetadataRoute.Sitemap = [{ url: `${STORE_URL}/`, changeFrequency: "weekly", priority: 1 }, { url: `${STORE_URL}/store`, changeFrequency: "daily", priority: 0.9 }];
    try {
      const products = await listAllStoreProducts();
      return [...fixed, ...products.map((p) => ({ url: productUrl(p.slug), changeFrequency: "daily" as const, priority: 0.7 }))];
    } catch {
      return fixed;
    }
  }
  return marketingSitemap();
}

function marketingSitemap(): MetadataRoute.Sitemap {
  const lastModified = new Date("2026-10-12");
  return [
    { url: `${SITE_URL}/`, lastModified, changeFrequency: "weekly", priority: 1 },
    { url: `${SITE_URL}/pricing`, lastModified, changeFrequency: "monthly", priority: 0.8 },
    { url: `${SITE_URL}/contact`, lastModified, changeFrequency: "yearly", priority: 0.5 },
    { url: `${SITE_URL}/privacy`, lastModified, changeFrequency: "yearly", priority: 0.3 },
    { url: `${SITE_URL}/terms`, lastModified, changeFrequency: "yearly", priority: 0.3 },
    { url: `${SITE_URL}/data-deletion`, lastModified, changeFrequency: "yearly", priority: 0.2 },
  ];
}
