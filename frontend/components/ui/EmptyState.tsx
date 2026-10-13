import { Inbox, type LucideIcon } from "lucide-react";


/** A default second line for the common empty lists, so a blank screen says what will appear and how it fills. */
const DEFAULT_HINTS: [RegExp, string][] = [
  [/^No customers/i, "People you work with appear here. Add one, or import a spreadsheet."],
  [/^No leads/i, "Enquiries from your website, calls and chat land here, each with a suggested next step."],
  [/^No quotes/i, "Quotes you send to customers show up here, with their status from draft to accepted."],
  [/^No invoices/i, "Invoices you create or approve appear here, with what is paid and what is still due."],
  [/^No contracts/i, "Agreements created from accepted quotes appear here, with who has viewed and signed them."],
  [/campaigns? yet|^No campaigns/i, "Campaigns you run to bring in or win back customers show up here, with their results."],
  [/^No referrals yet/i, "Referrals sent to clinics or partners appear here, with each one's current status."],
  [/^No tasks/i, "Work that needs a person lands here, so nothing is missed."],
  [/^No open exceptions/i, "Anything that looks wrong, such as a delayed job, is flagged here for you to resolve."],
  [/^No agents/i, "Agents you create take on governed tasks. Each one has its own permissions and audit trail."],
  [/^No content/i, "Posts and pages Klaros drafts for you appear here for review before anything is published."],
  [/^No workers/i, "Your field team appears here once added, so jobs can be assigned."],
];

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
  const shownHint = hint ?? DEFAULT_HINTS.find(([re]) => re.test(title))?.[1];
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
        {shownHint && <p className="text-[15px] leading-snug text-muted">{shownHint}</p>}
        {action && <div className="mt-1">{action}</div>}
      </div>
    </div>
  );
}
