import Link from "next/link";
import { ChevronRight, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/cn";

/**
 * Layout primitives for the signed-in app. Every page is built from the same few pieces so the whole
 * product reads as one system: a Page, titled Sections, inset grouped lists, big-number Tiles.
 */

export function Page({ children, className, narrow = false }: { children: React.ReactNode; className?: string; narrow?: boolean }) {
  return <div className={cn("mx-auto w-full px-5 py-8 sm:px-8 sm:py-12", narrow ? "max-w-3xl" : "max-w-6xl", className)}>{children}</div>;
}

/** The large page title: an optional eyebrow, a display-size heading, a lede, and actions to the right. */
export function Hero({ eyebrow, title, lede, actions, className }: { eyebrow?: React.ReactNode; title: React.ReactNode; lede?: React.ReactNode; actions?: React.ReactNode; className?: string }) {
  return (
    <header className={cn("mb-10 flex flex-col gap-5 sm:mb-12 sm:flex-row sm:items-end sm:justify-between", className)}>
      <div className="min-w-0">
        {eyebrow && <p className="mb-2 text-[15px] font-medium text-muted-foreground">{eyebrow}</p>}
        <h1 className="text-balance font-display text-[2.25rem] font-semibold leading-[1.05] tracking-[-0.03em] text-foreground sm:text-[3rem]">{title}</h1>
        {lede && <p className="mt-3 max-w-2xl text-[17px] leading-snug text-muted sm:text-xl">{lede}</p>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function Section({ id, title, hint, action, children, className }: { id?: string; title: string; hint?: string; action?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section id={id} aria-labelledby={id ? `${id}-h` : undefined} className={cn("mb-12 scroll-mt-20", className)}>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id={id ? `${id}-h` : undefined} className="font-display text-[1.5rem] font-semibold tracking-[-0.02em] text-foreground">
            {title}
          </h2>
          {hint && <p className="mt-1 text-[15px] text-muted">{hint}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

/** An inset grouped list: one rounded surface, rows divided by hairlines. The app's replacement for cards-in-a-column and plain tables. */
export function Group({ children, className, label }: { children: React.ReactNode; className?: string; label?: string }) {
  return (
    <ul aria-label={label} className={cn("divide-y divide-border/70 overflow-hidden rounded-3xl bg-surface-muted", className)}>
      {children}
    </ul>
  );
}

export function GroupRow({
  icon: Icon,
  title,
  detail,
  trailing,
  href,
  className,
}: {
  icon?: LucideIcon;
  title: React.ReactNode;
  detail?: React.ReactNode;
  trailing?: React.ReactNode;
  href?: string;
  className?: string;
}) {
  // A clickable row is one link around the TITLE, stretched over the whole row, so assistive tech announces just the title
  // while the whole row is still the click target.
  return (
    <li className={cn("group relative flex items-center gap-3.5 px-5 py-4 transition-colors duration-300", href && "hover:bg-black/[0.03]", className)}>
      {Icon && (
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-surface text-muted">
          <Icon className="h-[18px] w-[18px]" strokeWidth={1.75} aria-hidden="true" />
        </span>
      )}
      <span className="min-w-0 flex-1">
        <span className="block text-[15px] font-medium text-foreground">
          {href ? (
            <Link href={href} className="after:absolute after:inset-0 after:content-[''] focus:outline-none focus-visible:after:ring-2 focus-visible:after:ring-accent focus-visible:after:rounded-2xl">
              {title}
            </Link>
          ) : (
            title
          )}
        </span>
        {detail && <span className="mt-0.5 block text-[13px] leading-snug text-muted">{detail}</span>}
      </span>
      {trailing && <span className="flex shrink-0 items-center gap-2 text-[13px] text-muted">{trailing}</span>}
      {href && <ChevronRight className="h-4 w-4 shrink-0 text-border-strong transition-transform duration-300 ease-apple group-hover:translate-x-0.5" aria-hidden="true" />}
    </li>
  );
}

/** A big-number tile. Optional link. */
export function Tile({ label, value, hint, href, icon: Icon, className }: { label: string; value: React.ReactNode; hint?: React.ReactNode; href?: string; icon?: LucideIcon; className?: string }) {
  const inner = (
    <div className={cn("flex h-full flex-col justify-between rounded-3xl bg-surface-muted p-5 transition-all duration-500 ease-apple sm:p-6", href && "hover:-translate-y-0.5 hover:bg-surface hover:shadow-raised", className)}>
      <div className="flex items-start justify-between gap-2">
        <p className="text-[13px] font-medium text-muted">{label}</p>
        {Icon && <Icon className="h-[18px] w-[18px] text-muted-foreground" strokeWidth={1.75} aria-hidden="true" />}
      </div>
      <div className="mt-5">
        <p className={cn("font-display font-semibold leading-[1.05] tracking-[-0.03em] text-foreground", typeof value === "string" && value.length > 6 ? "text-[1.6rem]" : "text-[2.25rem] leading-none")}>{value}</p>
        {hint && <p className="mt-2 text-[13px] leading-snug text-muted">{hint}</p>}
      </div>
    </div>
  );
  return href ? (
    <Link href={href} className="block h-full">
      {inner}
    </Link>
  ) : (
    inner
  );
}

/** A circular progress ring with the fraction in the middle. */
export function ProgressRing({ done, total, size = 88 }: { done: number; total: number; size?: number }) {
  const stroke = 7;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const pct = total === 0 ? 0 : Math.min(1, done / total);
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }} role="img" aria-label={`${done} of ${total} complete`}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={stroke} className="stroke-border" />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - pct)}
          className="stroke-accent transition-[stroke-dashoffset] duration-1000 ease-apple"
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center leading-none">
        <span className="font-display text-xl font-semibold tracking-tight text-foreground">{done}</span>
        <span className="mt-0.5 text-[11px] text-muted-foreground">of {total}</span>
      </div>
    </div>
  );
}

/** A segmented control (the iOS tab strip) for filters and views. */
export function Segmented<T extends string>({ value, onChange, options, label }: { value: T; onChange: (v: T) => void; options: { value: T; label: string }[]; label: string }) {
  return (
    <div role="tablist" aria-label={label} className="inline-flex rounded-full bg-surface-muted p-1">
      {options.map((o) => (
        <button
          key={o.value}
          role="tab"
          aria-selected={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            "rounded-full px-4 py-1.5 text-[13px] font-medium transition-all duration-300 ease-apple",
            value === o.value ? "bg-surface text-foreground shadow-card" : "text-muted hover:text-foreground"
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
