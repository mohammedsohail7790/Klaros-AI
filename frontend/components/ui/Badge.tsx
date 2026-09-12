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
  // Attention / in-progress
  NEW: "bg-blue-50 text-blue-700 border-blue-200",
  CONTACTED: "bg-blue-50 text-blue-700 border-blue-200",
  PENDING: "bg-amber-50 text-amber-700 border-amber-200",
  REQUIRES_HUMAN: "bg-amber-50 text-amber-700 border-amber-200",
  SCHEDULED: "bg-amber-50 text-amber-700 border-amber-200",
  SENT: "bg-amber-50 text-amber-700 border-amber-200",
  IN_PROGRESS: "bg-amber-50 text-amber-700 border-amber-200",
  // Negative
  LOST: "bg-red-50 text-red-700 border-red-200",
  FAILED: "bg-red-50 text-red-700 border-red-200",
  BLOCKED: "bg-red-50 text-red-700 border-red-200",
  DECLINED: "bg-red-50 text-red-700 border-red-200",
  CANCELLED: "bg-red-50 text-red-700 border-red-200",
  ERROR: "bg-red-50 text-red-700 border-red-200",
  NOT_CONNECTED: "bg-surface-muted text-muted border-border-strong",
  NOT_IMPLEMENTED: "bg-surface-muted text-muted-foreground border-border",
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
