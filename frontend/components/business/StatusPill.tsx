import { CheckCircle2, CircleDashed, Clock, Link2, PlugZap, Settings2, XCircle } from "lucide-react";
import type { ReadinessState } from "@/lib/api";
import { cn } from "@/lib/cn";

/**
 * The one place the shared status vocabulary is rendered. The label and
 * colour for each state are fixed here so no screen can show a softer
 * claim than the backend made ("Connected" only ever renders for CONNECTED).
 */
export const STATE_META: Record<ReadinessState, { label: string; classes: string; icon: typeof CheckCircle2 }> = {
  CONNECTED: { label: "Connected", classes: "border-success/25 bg-success/[0.08] text-success", icon: CheckCircle2 },
  READY: { label: "Ready", classes: "border-success/25 bg-success/[0.08] text-success", icon: CheckCircle2 },
  CONFIGURATION_REQUIRED: {
    label: "Configuration required",
    classes: "border-warning/30 bg-warning/[0.09] text-warning",
    icon: Settings2,
  },
  NOT_CONNECTED: { label: "Not connected", classes: "border-border-strong bg-surface-muted text-muted", icon: PlugZap },
  NOT_READY: { label: "Not ready", classes: "border-danger/25 bg-danger/[0.06] text-danger", icon: XCircle },
  AVAILABLE: {
    label: "Available — not connected",
    classes: "border-accent/40 bg-accent-soft text-accent-hover",
    icon: Link2,
  },
  INTEGRATION_REQUIRED: {
    label: "Integration required",
    classes: "border-dashed border-border-strong bg-transparent text-muted",
    icon: CircleDashed,
  },
  PLANNED: { label: "Planned", classes: "border-dashed border-border-strong bg-transparent text-muted", icon: CircleDashed },
};

export function StatusPill({ state, className }: { state: ReadinessState | null | undefined; className?: string }) {
  if (!state) return null;
  const meta = STATE_META[state] ?? { label: state, classes: "border-border bg-surface-muted text-muted", icon: Clock };
  const Icon = meta.icon;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-medium",
        meta.classes,
        className
      )}
    >
      <Icon className="h-3 w-3" strokeWidth={2.25} aria-hidden="true" />
      {meta.label}
    </span>
  );
}
