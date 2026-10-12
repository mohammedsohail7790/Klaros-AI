import { Cairo, DM_Sans, Inter, Merriweather, Playfair_Display, Poppins, Roboto, Source_Serif_4, Tajawal } from "next/font/google";

/**
 * The faces a published website may use: the same closed list as the backend's ThemeTokens font allowlist. next/font downloads them at build time and
 * serves them from our own domain, so a visitor's browser never contacts Google (which matters on a health business's website). `preload: false` keeps a
 * page from fetching faces it does not use; the browser only requests the files for the families the theme actually names.
 */
// next/font reads these options at build time, so each call must use literal values (no shared object, no spread).
const inter = Inter({ display: "swap", preload: false, subsets: ["latin"], variable: "--site-font-inter" });
const roboto = Roboto({ display: "swap", preload: false, subsets: ["latin"], weight: ["400", "500", "700"], variable: "--site-font-roboto" });
const merriweather = Merriweather({ display: "swap", preload: false, subsets: ["latin"], weight: ["400", "700"], variable: "--site-font-merriweather" });
const poppins = Poppins({ display: "swap", preload: false, subsets: ["latin"], weight: ["400", "500", "600", "700"], variable: "--site-font-poppins" });
const playfair = Playfair_Display({ display: "swap", preload: false, subsets: ["latin"], variable: "--site-font-playfair" });
const dmSans = DM_Sans({ display: "swap", preload: false, subsets: ["latin"], variable: "--site-font-dm-sans" });
const sourceSerif = Source_Serif_4({ display: "swap", preload: false, subsets: ["latin"], variable: "--site-font-source-serif" });
const cairo = Cairo({ display: "swap", preload: false, subsets: ["arabic", "latin"], variable: "--site-font-cairo" });
const tajawal = Tajawal({ display: "swap", preload: false, subsets: ["arabic", "latin"], weight: ["400", "500", "700"], variable: "--site-font-tajawal" });

/** Put on the site's root element so the CSS variables below exist. */
export const SITE_FONT_CLASSES = [inter, roboto, merriweather, poppins, playfair, dmSans, sourceSerif, cairo, tajawal].map((f) => f.variable).join(" ");

const SANS = "system-ui, -apple-system, 'Segoe UI', sans-serif";
const SERIF = "Georgia, 'Times New Roman', serif";

/** Theme font name -> CSS font-family. An unknown name falls back to the system font: a theme can never inject an arbitrary family. */
const STACKS: Record<string, string> = {
  "system-ui": SANS,
  Georgia: SERIF,
  Inter: `var(--site-font-inter), ${SANS}`,
  Roboto: `var(--site-font-roboto), ${SANS}`,
  Merriweather: `var(--site-font-merriweather), ${SERIF}`,
  Poppins: `var(--site-font-poppins), ${SANS}`,
  "Playfair Display": `var(--site-font-playfair), ${SERIF}`,
  "DM Sans": `var(--site-font-dm-sans), ${SANS}`,
  "Source Serif 4": `var(--site-font-source-serif), ${SERIF}`,
  Cairo: `var(--site-font-cairo), Tahoma, ${SANS}`,
  Tajawal: `var(--site-font-tajawal), Tahoma, ${SANS}`,
};

export function fontStack(name: unknown): string {
  return typeof name === "string" && STACKS[name] ? STACKS[name] : SANS;
}

export const SITE_FONT_NAMES = Object.keys(STACKS);
