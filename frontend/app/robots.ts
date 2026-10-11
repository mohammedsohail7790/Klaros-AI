import type { MetadataRoute } from "next";
import { SITE_URL } from "@/lib/site";

/** Search engines may index the public marketing pages; the signed-in application and account pages are kept out. */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: ["/", "/pricing", "/privacy", "/terms", "/contact"],
        disallow: [
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
