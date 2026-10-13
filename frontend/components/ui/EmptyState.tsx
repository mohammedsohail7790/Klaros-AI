import { Inbox, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/cn";

export function EmptyState({
  icon: Icon = Inbox,
  title,
  action,
  compact = false,
}: {
  icon?: LucideIcon;
  title: string;
  action?: React.ReactNode;
  /** Tighter padding/icon for use nested inside an already-bordered card
   * (e.g. a list within a detail-page section) — the full-size version
   * is meant for a page's own primary content area. */
  compact?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center text-center",
        compact ? "gap-2 py-6" : "gap-4 rounded-3xl bg-surface-muted px-6 py-20"
      )}
    >
      <div
        className={cn(
          "flex items-center justify-center rounded-full bg-surface",
          compact ? "h-8 w-8" : "h-14 w-14"
        )}
      >
        <Icon className={compact ? "h-3.5 w-3.5 text-accent" : "h-5 w-5 text-accent"} strokeWidth={1.75} />
      </div>
      <p className={cn("max-w-md text-muted", compact ? "text-sm" : "text-[17px] leading-snug")}>{title}</p>
      {action}
    </div>
  );
}
