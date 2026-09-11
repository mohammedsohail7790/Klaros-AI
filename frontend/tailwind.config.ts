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
        card: "0 1px 2px 0 rgb(26 24 21 / 0.04), 0 1px 1px 0 rgb(26 24 21 / 0.03)",
        raised: "0 4px 16px -4px rgb(26 24 21 / 0.10), 0 2px 6px -2px rgb(26 24 21 / 0.06)",
        popover: "0 12px 32px -8px rgb(26 24 21 / 0.16), 0 4px 12px -4px rgb(26 24 21 / 0.08)",
      },
      borderRadius: {
        xl: "0.875rem",
      },
    },
  },
  plugins: [],
};

export default config;
