import { type LucideIcon, TrendingDown, TrendingUp } from "lucide-react";
import { cn } from "@/lib/cn";

/**
 * A dashboard stat tile: icon chip + label + value, with an optional
 * trend badge and an optional "tone" that tints the icon chip and value
 * for at-a-glance severity (e.g. overdue counts read as danger-tinted
 * without needing a separate banner). Replaces the plain
 * number-in-a-box pattern that was copy-pasted as a local `Stat`
 * component in a dozen-plus pages.
 */
export function StatCard({
  label,
  value,
  icon: Icon,
  tone = "neutral",
  trend,
  note,
  compact = false,
  className,
}: {
  label: string;
  value: string | number;
  icon?: LucideIcon;
  tone?: "neutral" | "accent" | "success" | "warning" | "danger";
  /** Positive numbers render as an upward/green trend, negative as downward/red. */
  trend?: number;
  /** A short explanatory footnote (e.g. why a metric reads "insufficient data"). */
  note?: string | null;
  /** Smaller value text for longer strings (timestamps, names) that would
   * otherwise wrap awkwardly at the default KPI-number size. */
  compact?: boolean;
  className?: string;
}) {
  const toneClasses: Record<string, string> = {
    neutral: "bg-surface-muted text-muted-foreground",
    accent: "bg-accent-soft text-accent",
    success: "bg-success/10 text-success",
    warning: "bg-warning/10 text-warning",
    danger: "bg-danger/10 text-danger",
  };

  return (
    <div
      className={cn(
        "klaros-card group flex flex-col gap-3 p-4 transition-all duration-200 ease-out hover:-translate-y-px hover:shadow-raised",
        className
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="text-xs font-medium text-muted">{label}</span>
        {Icon && (
          <span className={cn("flex h-7 w-7 shrink-0 items-center justify-center rounded-lg", toneClasses[tone])}>
            <Icon className="h-3.5 w-3.5" strokeWidth={2} />
          </span>
        )}
      </div>
      <div className="flex items-baseline gap-2">
        <span
          className={cn(
            "font-semibold leading-tight tracking-tight",
            compact ? "text-base" : "text-[1.65rem] leading-none",
            tone === "neutral" ? "text-foreground" : toneClasses[tone].split(" ")[1]
          )}
        >
          {value}
        </span>
        {typeof trend === "number" && trend !== 0 && (
          <span
            className={cn(
              "flex items-center gap-0.5 text-xs font-medium",
              trend > 0 ? "text-success" : "text-danger"
            )}
          >
            {trend > 0 ? <TrendingUp className="h-3 w-3" strokeWidth={2.5} /> : <TrendingDown className="h-3 w-3" strokeWidth={2.5} />}
            {Math.abs(trend)}%
          </span>
        )}
      </div>
      {note && <p className="text-xs leading-snug text-warning">{note}</p>}
    </div>
  );
}
