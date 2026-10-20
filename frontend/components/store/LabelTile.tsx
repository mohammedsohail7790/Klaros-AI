import { cn } from "@/lib/cn";

/**
 * The picture for a product, drawn like a product shot on a flat tile. With a supplier photo it shows the photo (multiplied onto the tile, so a white photo background
 * disappears). Without one it is a quiet monogram of the product's first letter, so a catalog with no photos still looks deliberate; a photo replaces it as soon as the
 * supplier's data has one. `tone` says what the tile sits on: "on-cream" (a sage tile on the page) or "on-sage" (a cream tile inside a sage card).
 */
export function ProductVisual({ name, imageUrl, size = "card", priority = false, tone = "on-cream" }: { name: string; slug?: string; category?: string | null; imageUrl: string | null; size?: "card" | "large" | "thumb" | "mini"; priority?: boolean; tone?: "on-cream" | "on-sage" }) {
  const bg = tone === "on-sage" ? "bg-background" : "bg-surface-muted";
  const radius = size === "thumb" ? "rounded-2xl" : size === "mini" ? "rounded-[20px]" : size === "large" ? "rounded-[32px]" : "rounded-[22px]";
  const initial = name.trim().slice(0, 1).toUpperCase();
  if (imageUrl) {
    return (
      <div className={cn("flex aspect-square items-center justify-center overflow-hidden", radius, bg)}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={imageUrl} alt={name} loading={priority ? "eager" : "lazy"} className="h-full w-full object-contain mix-blend-multiply" />
      </div>
    );
  }
  if (size === "mini") {
    return <div role="img" aria-label={name} className={cn("flex aspect-square items-center justify-center bg-background p-3 text-center", radius)}><span className="line-clamp-3 break-words text-[13px] font-semibold leading-[1.1] text-foreground sm:text-lg">{name}</span></div>;
  }
  const mark = size === "thumb" ? "text-2xl" : size === "large" ? "text-[9rem] sm:text-[13rem]" : "text-7xl sm:text-8xl";
  return (
    <div role="img" aria-label={name} className={cn("flex aspect-square items-center justify-center", radius, bg)}>
      <span aria-hidden className={cn("font-semibold leading-none tracking-[-0.05em] text-[rgb(var(--color-brand))]", mark)}>{initial}</span>
    </div>
  );
}
