import { Inbox, type LucideIcon } from "lucide-react";

/**
 * What a list shows before it has anything in it. Not an apology: a soft panel with a large icon, what this place is for, and the one thing to do next.
 * `compact` is the small version for use inside an already-framed card.
 */
export function EmptyState({
  icon: Icon = Inbox,
  title,
  hint,
  action,
  compact = false,
}: {
  icon?: LucideIcon;
  title: string;
  /** A second line saying what will appear here, or how it gets filled. */
  hint?: string;
  action?: React.ReactNode;
  /** Tighter padding/icon for use nested inside an already-bordered card. */
  compact?: boolean;
}) {
  if (compact) {
    return (
      <div className="flex flex-col items-center gap-2 py-6 text-center">
        <div className="flex h-8 w-8 items-center justify-center rounded-full bg-surface-muted">
          <Icon className="h-3.5 w-3.5 text-muted" strokeWidth={1.75} />
        </div>
        <p className="max-w-md text-sm text-muted">{title}</p>
        {action}
      </div>
    );
  }
  return (
    <div className="relative overflow-hidden rounded-3xl bg-surface-muted px-6 py-16 text-center sm:py-20">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0"
        style={{
          backgroundImage: "radial-gradient(rgb(var(--color-border-strong) / 0.55) 1px, transparent 1px)",
          backgroundSize: "22px 22px",
          maskImage: "radial-gradient(60% 70% at 50% 40%, black 0%, transparent 75%)",
          WebkitMaskImage: "radial-gradient(60% 70% at 50% 40%, black 0%, transparent 75%)",
        }}
      />
      <div className="relative mx-auto flex max-w-md flex-col items-center gap-4">
        <div className="relative">
          <span aria-hidden="true" className="absolute -inset-3 rounded-full bg-accent/10 blur-xl" />
          <div className="relative flex h-16 w-16 items-center justify-center rounded-full bg-surface shadow-raised ring-1 ring-border/70">
            <Icon className="h-7 w-7 text-accent" strokeWidth={1.5} />
          </div>
        </div>
        <p className="text-balance font-display text-[1.35rem] font-semibold leading-snug tracking-[-0.015em] text-foreground">{title}</p>
        {hint && <p className="text-[15px] leading-snug text-muted">{hint}</p>}
        {action && <div className="mt-1">{action}</div>}
      </div>
    </div>
  );
}
