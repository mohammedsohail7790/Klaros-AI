// @vitest-environment node
import { readFileSync } from "node:fs";
import path from "node:path";
import { NextRequest } from "next/server";
import { describe, expect, it, vi } from "vitest";
import { proxy } from "@/proxy";

const css = readFileSync(path.join(__dirname, "../../globals.css"), "utf8");
const theme = css.slice(css.indexOf(".cleanroom-theme {"));
const token = (name: string): [number, number, number] => {
  const m = theme.match(new RegExp(`--color-${name}:\\s*(\\d+) (\\d+) (\\d+);`));
  if (!m) throw new Error(`token ${name} missing`);
  return [Number(m[1]), Number(m[2]), Number(m[3])];
};
const lum = ([r, g, b]: number[]) => {
  const f = (c: number) => ((c /= 255) <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
};
const contrast = (a: number[], b: number[]) => {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};
const rgb = (hex: string): [number, number, number] => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)) as [number, number, number];

describe("Clean Room palette", () => {
  const cream = rgb("#FBF9F5"), green = rgb("#2D5A27"), apricot = rgb("#E87A40"), charcoal = rgb("#1A201C"), sage = rgb("#E2E9E1");
  const mix = (fg: number[], bg: number[], a: number) => fg.map((c, i) => Math.round(c * a + bg[i] * (1 - a)));

  it("uses the five brand colours exactly", () => {
    expect(token("background")).toEqual(cream);       // Soft Cream White
    expect(token("brand")).toEqual(green);            // Meadow Green
    expect(token("accent")).toEqual(apricot);         // Warm Apricot
    expect(token("foreground")).toEqual(charcoal);    // Deep Charcoal
    expect(token("surface-muted")).toEqual(sage);     // Sage Mist
    expect(token("surface")).toEqual(cream);
  });

  it("introduces no other colour: the only extra tones are Charcoal at lower strength", () => {
    expect(token("muted")).toEqual(mix(charcoal, cream, 0.7));      // secondary text: Charcoal at 70%
    expect(token("border")).toEqual(mix(charcoal, cream, 0.16));    // hairlines: Charcoal at 16%
    expect(token("accent-hover")).toEqual(apricot);                 // hover dims the button, it does not use a new colour
    expect(token("accent-foreground")).toEqual(charcoal);
    const allowed = new Set([cream, green, apricot, charcoal, sage, token("muted"), token("border")].map((c) => c.join(" ")));
    const tokens = [...theme.slice(0, theme.indexOf("}")).matchAll(/--color-[a-z0-9-]+:\s*(\d+ \d+ \d+);/g)].map((m) => m[1]);
    expect(tokens.length).toBeGreaterThan(10);
    for (const t of tokens) expect(allowed.has(t), t).toBe(true);
  });

  it("keeps every text and background pair readable (WCAG AA, 4.5:1)", () => {
    const pairs: [string, number[], number[]][] = [
      ["text on page", charcoal, cream],
      ["secondary text on page", token("muted"), cream],
      ["secondary text on sage tiles", token("muted"), sage],
      ["button text on apricot", charcoal, apricot],
      ["cream on the green hero and tiles", cream, green],
      ["green links and marks on page", green, cream],
      ["green links on sage", green, sage],
      ["text on sage chips and tiles", charcoal, sage],
      ["cream on the charcoal bar and tiles", cream, charcoal],
      ["cream at 80% on the green hero", mix(cream, green, 0.8), green],
      ["cream at 70% on charcoal tiles", mix(cream, charcoal, 0.7), charcoal],
    ];
    for (const [name, a, b] of pairs) expect(contrast(a, b), name).toBeGreaterThanOrEqual(4.5);
  });

  it("is scoped to the store, so the rest of Klaros keeps its own look", () => {
    expect(css.slice(0, css.indexOf(".cleanroom-theme {"))).toContain("--color-accent: 0 102 204;");
    expect(theme.match(/^\S.*\{$/gm)?.every((sel) => sel.startsWith(".cleanroom-theme") || sel.startsWith("@keyframes") || sel.startsWith("@media"))).toBe(true);
  });
});

describe("the store's own domain", () => {
  const req = (url: string, host: string) => new NextRequest(url, { headers: { host } });

  it("shows the shop at the home page of yourcleanroom.com and www.yourcleanroom.com", async () => {
    for (const host of ["yourcleanroom.com", "www.yourcleanroom.com"]) {
      const res = await proxy(req(`https://${host}/`, host));
      expect(res.headers.get("x-middleware-rewrite"), host).toBe(`https://${host}/store`);
    }
  });

  it("serves every other path on that domain as it is, without asking the backend", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const res = await proxy(req("https://www.yourcleanroom.com/store/cart", "www.yourcleanroom.com"));
    expect(res.headers.get("x-middleware-rewrite")).toBeNull();
    expect(res.headers.get("x-middleware-next")).toBe("1");
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("leaves the platform's own hosts alone", async () => {
    const res = await proxy(req("https://meetklaros.com/", "meetklaros.com"));
    expect(res.headers.get("x-middleware-rewrite")).toBeNull();
    expect(res.headers.get("x-middleware-next")).toBe("1");
  });

  it("does not treat a look-alike domain as the store", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 404 }));
    const res = await proxy(req("https://yourcleanroom.com.evil.example/", "yourcleanroom.com.evil.example"));
    expect(res.status).toBe(404);
    vi.restoreAllMocks();
  });
});
