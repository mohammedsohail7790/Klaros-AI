"use client";

/**
 * Designed visuals for a generated site, drawn from the site's own colours. A product or section with no photo gets a gradient tile with an icon chosen
 * from the words in its title - never a placeholder box, and never a made-up photo. An owner-supplied https image replaces it everywhere it is offered.
 */

import {
  Armchair,
  Baby,
  BookOpen,
  Briefcase,
  Cake,
  Camera,
  Car,
  Code2,
  Coffee,
  Dog,
  Dumbbell,
  Flower2,
  GraduationCap,
  Gem,
  Headphones,
  HeartPulse,
  House,
  Laptop,
  Lamp,
  Monitor,
  Music,
  Package,
  Paintbrush,
  PenTool,
  Plane,
  Scissors,
  Shirt,
  ShoppingBag,
  Smartphone,
  Sofa,
  Sparkles,
  Stethoscope,
  Truck,
  UtensilsCrossed,
  Wine,
  Wrench,
  type LucideIcon,
} from "lucide-react";

const ICON_RULES: [RegExp, LucideIcon][] = [
  [/monitor|screen|display|riser|desk|workspace|office/i, Monitor],
  [/laptop|notebook|computer|stand/i, Laptop],
  [/chair|seat|ergonom|posture/i, Armchair],
  [/sofa|couch|furniture|living|decor/i, Sofa],
  [/lamp|light/i, Lamp],
  [/headphone|audio|speaker|sound|podcast/i, Headphones],
  [/phone|mobile|app\b/i, Smartphone],
  [/shirt|cloth|apparel|fashion|wear|dress/i, Shirt],
  [/coffee|cafe|tea|brew/i, Coffee],
  [/restaurant|food|meal|kitchen|catering|dining/i, UtensilsCrossed],
  [/cake|bak|dessert|pastry/i, Cake],
  [/wine|bar\b|drink|beverage/i, Wine],
  [/code|software|develop|saas|web|digital|tech/i, Code2],
  [/health|medical|wellness|care|therapy|patient/i, HeartPulse],
  [/clinic|doctor|dental|hospital|surgery|physio/i, Stethoscope],
  [/car|auto|vehicle|garage|tyre|tire/i, Car],
  [/home|house|property|real estate|clean/i, House],
  [/repair|plumb|electric|handyman|tool|maintenance|hvac/i, Wrench],
  [/photo|camera|video|film/i, Camera],
  [/fitness|gym|yoga|train|sport|exercise/i, Dumbbell],
  [/book|course|learn|tutor|education|class|school/i, BookOpen],
  [/graduat|academy|study|student/i, GraduationCap],
  [/hair|salon|barber|beauty|nail|spa/i, Scissors],
  [/flower|garden|plant|florist/i, Flower2],
  [/music|band|lesson|concert/i, Music],
  [/jewel|gem|ring|watch|luxury/i, Gem],
  [/travel|tour|flight|trip|holiday/i, Plane],
  [/pet|dog|cat|vet/i, Dog],
  [/baby|kid|child|toy/i, Baby],
  [/design|brand|creative|studio|art\b/i, PenTool],
  [/paint|decorat|interior/i, Paintbrush],
  [/ship|deliver|logistic|freight|courier|supplier/i, Truck],
  [/consult|business|agency|advis|finance|account|legal/i, Briefcase],
  [/shop|store|retail|accessor|product/i, ShoppingBag],
];

export function iconFor(text: string): LucideIcon {
  for (const [pattern, icon] of ICON_RULES) if (pattern.test(text)) return icon;
  return text.trim() ? Package : Sparkles;
}

/** https images only - the same rule the backend enforces, re-checked here before anything is put in an <img>. */
export function safeImage(v: unknown): string | undefined {
  return typeof v === "string" && /^https:\/\//i.test(v.trim()) ? v.trim() : undefined;
}

const ANGLES = [135, 200, 160, 220, 120, 250];

export function ArtTile({ text, index = 0, image, className = "" }: { text: string; index?: number; image?: unknown; className?: string }) {
  const src = safeImage(image);
  if (src) {
    // eslint-disable-next-line @next/next/no-img-element
    return <img className={`klaros-site-art klaros-site-art-photo ${className}`.trim()} src={src} alt="" loading="lazy" referrerPolicy="no-referrer" />;
  }
  const Icon = iconFor(text);
  const angle = ANGLES[index % ANGLES.length];
  const lift = (index * 9) % 22; // a little variety in lightness from card to card, all within the brand colour
  return (
    <div
      className={`klaros-site-art ${className}`.trim()}
      aria-hidden="true"
      style={{
        background: `radial-gradient(70% 60% at 85% 10%, color-mix(in srgb, var(--klaros-site-secondary) 55%, transparent) 0%, transparent 70%), linear-gradient(${angle}deg, color-mix(in srgb, var(--klaros-site-primary) ${100 - lift}%, #fff) 0%, color-mix(in srgb, var(--klaros-site-primary) 62%, #000) 100%)`,
      }}
    >
      <span className="klaros-site-art-orb klaros-site-art-orb-a" />
      <span className="klaros-site-art-orb klaros-site-art-orb-b" />
      <span className="klaros-site-art-glass">
        <Icon strokeWidth={1.5} />
      </span>
    </div>
  );
}
