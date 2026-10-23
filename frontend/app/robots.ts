import type { MetadataRoute } from "next";
import { headers } from "next/headers";
import { SITE_URL } from "@/lib/site";
import { STORE_URL } from "@/lib/storeConfig";
import { isStoreHost } from "@/lib/storeSeo";

/**
 * One app serves several hosts. On the store's own host: the shop may be crawled, the basket, checkout and order pages (private, per shopper) may not, and the store's own sitemap
 * is named (not the Klaros marketing one). Every other host gets the marketing rules below, unchanged.
 */
export default async function robots(): Promise<MetadataRoute.Robots> {
  if (isStoreHost((await headers()).get("host"))) {
    return {
      rules: { userAgent: "*", allow: "/", disallow: ["/store/cart", "/store/checkout", "/store/orders", "/api/", "/dropshipping", "/dashboard", "/business", "/leads", "/settings", "/login"] },
      sitemap: `${STORE_URL}/sitemap.xml`,
    };
  }
  return marketingRobots();
}

/** Search engines may index the public marketing pages; the signed-in application and account pages are kept out. */
function marketingRobots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: ["/", "/pricing", "/privacy", "/terms", "/contact", "/store"],
        disallow: [
          "/store/cart",
          "/store/checkout",
          "/store/orders",
          "/dropshipping",
          "/dashboard",
          "/business",
          "/medical-tourism",
          "/leads",
          "/customers",
          "/settings",
          "/setup",
          "/onboarding",
          "/login",
          "/register",
          "/forgot-password",
          "/reset-password",
          "/accept-invite",
        ],
      },
    ],
    sitemap: `${SITE_URL}/sitemap.xml`,
  };
}
