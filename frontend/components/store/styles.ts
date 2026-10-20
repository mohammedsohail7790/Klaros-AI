import { cn } from "@/lib/cn";

// Plain class strings for the storefront's buttons and links. Kept out of the "use client" component file so server pages (the shop and product pages) can use them too.
// Buttons are pills, the way apple.com draws them; the single apricot fill is reserved for the one action that matters on a screen.
type Variant = "primary" | "secondary" | "light" | "ghost";
type Size = "sm" | "md" | "lg";

const BASE = "cr-press inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-full font-medium tracking-[-0.01em] transition-[filter] duration-150 disabled:pointer-events-none disabled:opacity-40";
const VARIANT: Record<Variant, string> = {
  primary: "bg-accent text-accent-foreground",
  secondary: "border border-[rgb(var(--color-brand))] text-[rgb(var(--color-brand))] hover:bg-[rgb(var(--color-brand))] hover:text-[rgb(var(--color-background))]",
  // on a dark panel: a cream outline
  light: "border border-[rgb(var(--color-background))] text-[rgb(var(--color-background))] hover:bg-[rgb(var(--color-background))] hover:text-[rgb(var(--color-foreground))]",
  ghost: "text-muted hover:bg-surface-muted hover:text-foreground",
};
const SIZE: Record<Size, string> = { sm: "h-8 px-4 text-sm", md: "h-11 px-6 text-[17px]", lg: "h-[52px] px-8 text-[19px]" };

export const buttonClass = (variant: Variant = "primary", size: Size = "md", extra?: string) => cn(BASE, VARIANT[variant], SIZE[size], extra);

/** The "Shop all products ›" text link, in brand green (or cream on a dark panel). */
export const linkClass = (onDark = false) => cn("inline-flex items-center gap-0.5 text-[19px] hover:underline underline-offset-4", onDark ? "text-[rgb(var(--color-background))]" : "text-[rgb(var(--color-brand))]");
