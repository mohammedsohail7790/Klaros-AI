import { cn } from "@/lib/cn";

const STATUS_VARIANTS: Record<string, string> = {
  // Positive / success-shaped statuses
  ACTIVE: "bg-emerald-50 text-emerald-700 border-emerald-200",
  CONNECTED: "bg-emerald-50 text-emerald-700 border-emerald-200",
  COMPLETED: "bg-emerald-50 text-emerald-700 border-emerald-200",
  PAID: "bg-emerald-50 text-emerald-700 border-emerald-200",
  BOOKED: "bg-emerald-50 text-emerald-700 border-emerald-200",
  QUALIFIED: "bg-emerald-50 text-emerald-700 border-emerald-200",
  APPROVED: "bg-emerald-50 text-emerald-700 border-emerald-200",
  ACCEPTED: "bg-emerald-50 text-emerald-700 border-emerald-200",
  SUCCEEDED: "bg-emerald-50 text-emerald-700 border-emerald-200",
  ENABLED: "bg-emerald-50 text-emerald-700 border-emerald-200",
  EXECUTED: "bg-emerald-50 text-emerald-700 border-emerald-200",
  PROCESSED: "bg-emerald-50 text-emerald-700 border-emerald-200",
  DEPOSIT_PAID: "bg-emerald-50 text-emerald-700 border-emerald-200",
  CONVERTED: "bg-emerald-50 text-emerald-700 border-emerald-200",
  success: "bg-emerald-50 text-emerald-700 border-emerald-200",
  // Attention / in-progress
  NEW: "bg-blue-50 text-blue-700 border-blue-200",
  CONTACTED: "bg-blue-50 text-blue-700 border-blue-200",
  VIEWED: "bg-blue-50 text-blue-700 border-blue-200",
  RUNNING: "bg-blue-50 text-blue-700 border-blue-200",
  EXECUTING: "bg-blue-50 text-blue-700 border-blue-200",
  PUBLISHED: "bg-blue-50 text-blue-700 border-blue-200",
  PROCESSING: "bg-blue-50 text-blue-700 border-blue-200",
  SYSTEM: "bg-blue-50 text-blue-700 border-blue-200",
  PENDING: "bg-amber-50 text-amber-700 border-amber-200",
  REQUIRES_HUMAN: "bg-amber-50 text-amber-700 border-amber-200",
  SCHEDULED: "bg-amber-50 text-amber-700 border-amber-200",
  SENT: "bg-amber-50 text-amber-700 border-amber-200",
  IN_PROGRESS: "bg-amber-50 text-amber-700 border-amber-200",
  WAITING: "bg-amber-50 text-amber-700 border-amber-200",
  RETRYING: "bg-amber-50 text-amber-700 border-amber-200",
  MEDIUM: "bg-amber-50 text-amber-700 border-amber-200",
  DISABLED: "bg-amber-50 text-amber-700 border-amber-200",
  DEPOSIT_PENDING: "bg-amber-50 text-amber-700 border-amber-200",
  EXPIRING_SOON: "bg-amber-50 text-amber-700 border-amber-200",
  AI: "bg-purple-50 text-purple-700 border-purple-200",
  // Negative
  CRITICAL: "bg-red-50 text-red-700 border-red-200",
  HIGH: "bg-red-50 text-red-700 border-red-200",
  LOST: "bg-red-50 text-red-700 border-red-200",
  FAILED: "bg-red-50 text-red-700 border-red-200",
  BLOCKED: "bg-red-50 text-red-700 border-red-200",
  DECLINED: "bg-red-50 text-red-700 border-red-200",
  CANCELLED: "bg-red-50 text-red-700 border-red-200",
  ERROR: "bg-red-50 text-red-700 border-red-200",
  REJECTED: "bg-red-50 text-red-700 border-red-200",
  REVOKED: "bg-red-50 text-red-700 border-red-200",
  EXPIRED: "bg-red-50 text-red-700 border-red-200",
  DEAD_LETTER: "bg-red-50 text-red-700 border-red-200",
  failure: "bg-red-50 text-red-700 border-red-200",
  // Neutral
  NOT_CONNECTED: "bg-surface-muted text-muted border-border-strong",
  NOT_IMPLEMENTED: "bg-surface-muted text-muted-foreground border-border",
  DRAFT: "bg-surface-muted text-muted border-border-strong",
  ARCHIVED: "bg-surface-muted text-muted border-border-strong",
  NOT_STARTED: "bg-surface-muted text-muted border-border-strong",
  USER: "bg-surface-muted text-muted border-border-strong",
};

export function Badge({
  status,
  children,
  className,
}: {
  status?: string;
  children: React.ReactNode;
  className?: string;
}) {
  const variant = status ? STATUS_VARIANTS[status] : undefined;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium",
        variant ?? "border-border-strong bg-surface-muted text-muted",
        className
      )}
    >
      {children}
    </span>
  );
}
