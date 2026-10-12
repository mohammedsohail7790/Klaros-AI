/**
 * The hand-checked looks a website owner can pick in the theme editor. These mirror backend/app/services/website_brand.py (which chooses one when a site
 * is first generated); the backend re-validates whatever is saved, so a mismatch can never store an invalid theme. Every look passes a 7:1 contrast
 * check for body text and for white text on its primary colour (see lib/__tests__/brandLooks.test.ts).
 */
export interface BrandLook {
  key: string;
  label: string;
  primary_color: string;
  secondary_color: string;
  surface_color: string;
  text_color: string;
  heading_font: string;
  body_font: string;
}

export const BRAND_LOOKS: BrandLook[] = [
  { key: "harbour", label: "Harbour", primary_color: "#14304F", secondary_color: "#E0A030", surface_color: "#F2F5F9", text_color: "#14243A", heading_font: "Playfair Display", body_font: "Inter" },
  { key: "lagoon", label: "Lagoon", primary_color: "#0F5C5A", secondary_color: "#E27D60", surface_color: "#EFF6F5", text_color: "#12312F", heading_font: "DM Sans", body_font: "DM Sans" },
  { key: "orchid", label: "Orchid", primary_color: "#4B2A5E", secondary_color: "#D4A24C", surface_color: "#F6F1F8", text_color: "#2A1636", heading_font: "Merriweather", body_font: "Inter" },
  { key: "cedar", label: "Cedar", primary_color: "#234A3A", secondary_color: "#C9A66B", surface_color: "#F2F5F0", text_color: "#17261F", heading_font: "Source Serif 4", body_font: "Inter" },
  { key: "slate", label: "Slate", primary_color: "#27364B", secondary_color: "#4FA3D1", surface_color: "#F1F4F8", text_color: "#1B2635", heading_font: "Poppins", body_font: "Inter" },
];

/** The same closed list as the backend's font allowlist. */
export const FONT_OPTIONS = ["system-ui", "Inter", "Georgia", "Roboto", "Merriweather", "Poppins", "Playfair Display", "DM Sans", "Source Serif 4", "Cairo", "Tajawal"];

function luminance(hex: string): number {
  const channel = (c: number) => {
    const v = c / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  const n = (i: number) => parseInt(hex.slice(i, i + 2), 16);
  return 0.2126 * channel(n(1)) + 0.7152 * channel(n(3)) + 0.0722 * channel(n(5));
}

export function contrastRatio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}
