import { describe, expect, it } from "vitest";
import robots from "@/app/robots";
// next.config.js is plain CommonJS
// eslint-disable-next-line @typescript-eslint/no-require-imports
const nextConfig = require("../../../next.config.js");

describe("search engines", () => {
  it("may read the shop, but not the basket, checkout, orders or the staff pages", () => {
    const rule = (robots().rules as { allow: string[]; disallow: string[] }[])[0];
    expect(rule.allow).toContain("/store");
    for (const p of ["/store/cart", "/store/checkout", "/store/orders", "/dropshipping"]) expect(rule.disallow).toContain(p);
  });

  it("also tells them in a header, for every private store and staff page", async () => {
    const headers: { source: string; headers: { key: string; value: string }[] }[] = await nextConfig.headers();
    const noindex = headers.filter((h) => h.headers.some((x) => x.key === "X-Robots-Tag")).map((h) => h.source);
    for (const s of ["/store/cart", "/store/checkout", "/store/orders/:path*", "/dropshipping/:path*"]) expect(noindex).toContain(s);
    expect(noindex).not.toContain("/store");
  });
});
