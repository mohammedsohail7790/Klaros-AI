import type { Config } from "tailwindcss";

function rgbVar(name: string) {
  return `rgb(var(${name}) / <alpha-value>)`;
}

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        background: rgbVar("--color-background"),
        surface: rgbVar("--color-surface"),
        "surface-muted": rgbVar("--color-surface-muted"),
        border: {
          DEFAULT: rgbVar("--color-border"),
          strong: rgbVar("--color-border-strong"),
        },
        foreground: rgbVar("--color-foreground"),
        muted: {
          DEFAULT: rgbVar("--color-muted"),
          foreground: rgbVar("--color-muted-foreground"),
        },
        accent: {
          DEFAULT: rgbVar("--color-accent"),
          hover: rgbVar("--color-accent-hover"),
          foreground: rgbVar("--color-accent-foreground"),
          soft: rgbVar("--color-accent-soft"),
          2: rgbVar("--color-accent-2"),
        },
        success: rgbVar("--color-success"),
        warning: rgbVar("--color-warning"),
        danger: rgbVar("--color-danger"),
      },
      fontFamily: {
        display: ["var(--font-display)", "serif"],
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      boxShadow: {
        // card: deliberately left subtle — this tier sits behind dense data
        // tables/stat tiles across the dashboard, where a heavy shadow reads
        // as noise, not polish (same restraint Stripe/Linear apply to their
        // own dashboards).
        card: "0 1px 2px 0 rgb(26 24 21 / 0.04), 0 1px 1px 0 rgb(26 24 21 / 0.03)",
        // raised/popover: genuinely elevated further for surfaces that
        // should feel lifted off the page (dropdowns, modals, hover states)
        // — matching the more generous, softer shadow scale used across the
        // Halla AI family's own marketing site (see --shadow-lg/--shadow-xl).
        raised: "0 6px 20px -6px rgb(26 24 21 / 0.14), 0 3px 10px -4px rgb(26 24 21 / 0.08)",
        popover: "0 24px 60px -12px rgb(26 24 21 / 0.22), 0 8px 24px -6px rgb(26 24 21 / 0.10)",
        // glow: a colored, accent-tinted shadow for primary-button/CTA hover
        // states — the same "hover adds a soft glow in the accent color"
        // treatment Halla's own primary buttons use.
        glow: "0 10px 28px -6px rgb(var(--color-accent) / 0.38)",
      },
      borderRadius: {
        xl: "0.875rem",
      },
    },
  },
  plugins: [],
};

export default config;
