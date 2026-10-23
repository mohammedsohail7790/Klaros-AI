import { beforeEach, describe, expect, it, vi } from "vitest";
import { INFO, product, renderInStore } from "./fixtures";

const api = vi.hoisted(() => ({ getStoreInfo: vi.fn(), listStoreProducts: vi.fn(), listAllStoreProducts: vi.fn(), getStoreProduct: vi.fn() }));
vi.mock("@/lib/storeApi", async () => ({ ...(await vi.importActual<object>("@/lib/storeApi")), ...api }));
const host = vi.hoisted(() => ({ value: "www.yourcleanroom.com" as string | null }));
vi.mock("next/headers", () => ({ headers: async () => ({ get: (k: string) => (k === "host" ? host.value : null) }) }));
import robots from "@/app/robots";
import sitemap from "@/app/sitemap";
import ProductPage, { generateMetadata } from "@/app/store/products/[slug]/page";
import { isStoreHost, jsonLdScript, productJsonLd } from "@/lib/storeSeo";

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  host.value = "www.yourcleanroom.com";
  api.getStoreInfo.mockResolvedValue(INFO);
  api.listStoreProducts.mockResolvedValue([product()]);
  api.listAllStoreProducts.mockResolvedValue([product(), product({ name: "Brown Rice", slug: "brown-rice" })]);
  api.getStoreProduct.mockResolvedValue(product());
});

describe("which hosts are the store", () => {
  it("knows the store's hosts, with or without a port, and nothing else", () => {
    expect(isStoreHost("www.yourcleanroom.com")).toBe(true);
    expect(isStoreHost("YourCleanRoom.com:443")).toBe(true);
    expect(isStoreHost("meetklaros.com")).toBe(false);
    expect(isStoreHost("evil.com")).toBe(false);
    expect(isStoreHost(null)).toBe(false);
  });
});

describe("robots.txt", () => {
  it("on the store host lets the shop be crawled, keeps private pages out and names the store's own sitemap", async () => {
    const r = await robots();
    const rule = Array.isArray(r.rules) ? r.rules[0] : r.rules;
    expect(rule.allow).toBe("/");
    expect(rule.disallow).toEqual(expect.arrayContaining(["/store/cart", "/store/checkout", "/store/orders", "/dropshipping"]));
    expect(r.sitemap).toBe("https://www.yourcleanroom.com/sitemap.xml");
  });

  it("on any other host is the Klaros marketing file, unchanged", async () => {
    host.value = "meetklaros.com";
    const r = await robots();
    expect(String(r.sitemap)).not.toContain("yourcleanroom");
    expect(JSON.stringify(r.rules)).toContain("/pricing");
  });
});

describe("sitemap.xml", () => {
  it("on the store host lists the welcome page, the shop and each product, all on the store's own address", async () => {
    const urls = (await sitemap()).map((s) => s.url);
    expect(urls).toEqual(["https://www.yourcleanroom.com/", "https://www.yourcleanroom.com/store", "https://www.yourcleanroom.com/store/products/green-tea", "https://www.yourcleanroom.com/store/products/brown-rice"]);
  });

  it("lists only the two fixed pages when the shop cannot be reached, and never errors", async () => {
    api.listAllStoreProducts.mockRejectedValueOnce(new Error("down"));
    expect((await sitemap()).map((s) => s.url)).toEqual(["https://www.yourcleanroom.com/", "https://www.yourcleanroom.com/store"]);
  });

  it("on any other host is the Klaros marketing sitemap, unchanged", async () => {
    host.value = "meetklaros.com";
    const urls = (await sitemap()).map((s) => s.url);
    expect(urls.some((u) => u.endsWith("/pricing"))).toBe(true);
    expect(urls.some((u) => u.includes("yourcleanroom"))).toBe(false);
    expect(api.listAllStoreProducts).not.toHaveBeenCalled();
  });
});

describe("product data for search engines", () => {
  it("states only facts the shop holds: name, price, stock and picture, with no ratings or reviews", () => {
    const data = productJsonLd(product({ image_url: "https://img.example/tea.jpg" })) as Record<string, any>;
    expect(data["@type"]).toBe("Product");
    expect(data.name).toBe("Green Tea");
    expect(data.image).toBe("https://img.example/tea.jpg");
    expect(data.offers[0]).toMatchObject({ "@type": "Offer", price: "10.00", priceCurrency: "USD", availability: "https://schema.org/InStock", url: "https://www.yourcleanroom.com/store/products/green-tea" });
    expect(JSON.stringify(data)).not.toMatch(/aggregateRating|review|ratingValue/i);
  });

  it("says out of stock when an item is, so a result never promises what cannot be bought", () => {
    const data = productJsonLd(product({ items: [{ sku: "T", price: "10.00", currency: "USD", in_stock: false }] })) as Record<string, any>;
    expect(data.offers[0].availability).toBe("https://schema.org/OutOfStock");
  });

  it("escapes '<' so a product name can never close the script tag", () => {
    const text = jsonLdScript(productJsonLd(product({ name: "</script><b>x" })));
    expect(text).not.toContain("</script>");
    expect(JSON.parse(text).name).toBe("</script><b>x");
  });

  it("is on the product page, with a canonical link and a social preview", async () => {
    const { container } = renderInStore(await ProductPage({ params: Promise.resolve({ slug: "green-tea" }) }));
    const script = container.querySelector("script[type='application/ld+json']");
    expect(JSON.parse(script!.textContent ?? "{}").name).toBe("Green Tea");
    const meta = await generateMetadata({ params: Promise.resolve({ slug: "green-tea" }) });
    expect(meta.alternates?.canonical).toBe("https://www.yourcleanroom.com/store/products/green-tea");
    expect(meta.openGraph).toMatchObject({ title: "Green Tea", siteName: "Clean Room" });
  });
});
