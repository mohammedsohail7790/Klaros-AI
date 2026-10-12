import { describe, expect, it } from "vitest";
import { BRAND_LOOKS, FONT_OPTIONS, contrastRatio } from "@/lib/brandLooks";
import { SITE_FONT_NAMES } from "@/lib/siteFonts";

describe("brand looks", () => {
  it.each(BRAND_LOOKS.map((l) => [l.key, l] as const))("%s is legible: body text, white button text and tinted bands all pass 7:1", (_key, look) => {
    expect(contrastRatio(look.text_color, "#FFFFFF")).toBeGreaterThanOrEqual(7);
    expect(contrastRatio("#FFFFFF", look.primary_color)).toBeGreaterThanOrEqual(7);
    expect(contrastRatio(look.text_color, look.surface_color)).toBeGreaterThanOrEqual(7);
  });

  it("only uses fonts the site can actually load", () => {
    for (const look of BRAND_LOOKS) {
      expect(FONT_OPTIONS).toContain(look.heading_font);
      expect(FONT_OPTIONS).toContain(look.body_font);
    }
    expect([...FONT_OPTIONS].sort()).toEqual([...SITE_FONT_NAMES].sort());
  });

  it("measures contrast the standard way", () => {
    expect(contrastRatio("#000000", "#FFFFFF")).toBeCloseTo(21, 0);
    expect(contrastRatio("#777777", "#FFFFFF")).toBeLessThan(4.5);
  });
});
