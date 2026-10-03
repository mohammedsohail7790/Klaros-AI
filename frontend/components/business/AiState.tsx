import { Bot, CalendarCheck, CalendarClock, CheckCircle2, CircleDashed, Hourglass, MessageCircle, UserRound } from "lucide-react";
import type { InteractionState } from "@/lib/api";
import { cn } from "@/lib/cn";

// The label and tone for every AI-interaction state, fixed in one place so no screen can
// soften or embellish what the backend derived ("Qualified" only for a recorded qualification).
export const AI_STATE_META: Record<InteractionState, { label: string; tone: string; icon: typeof Bot }> = {
  NOT_CONNECTED: { label: "Halla not connected", tone: "border-dashed border-border-strong text-muted", icon: CircleDashed },
  CONFIGURATION_REQUIRED: { label: "Halla needs configuration", tone: "border-warning/30 bg-warning/[0.09] text-warning", icon: CircleDashed },
  WAITING_FOR_HALLA: { label: "Waiting for Halla", tone: "border-border-strong bg-surface-muted text-muted", icon: Hourglass },
  IN_PROGRESS: { label: "Halla is talking to the customer", tone: "border-accent/40 bg-accent-soft text-accent-hover", icon: MessageCircle },
  QUALIFICATION_PENDING: { label: "Qualification pending", tone: "border-border-strong bg-surface-muted text-muted", icon: Hourglass },
  QUALIFIED: { label: "Qualified", tone: "border-success/25 bg-success/[0.08] text-success", icon: CheckCircle2 },
  ESCALATED: { label: "Escalated to a person", tone: "border-warning/30 bg-warning/[0.09] text-warning", icon: UserRound },
  APPOINTMENT_REQUESTED: { label: "Appointment requested", tone: "border-accent/40 bg-accent-soft text-accent-hover", icon: CalendarClock },
  APPOINTMENT_CONFIRMED: { label: "Appointment confirmed", tone: "border-success/25 bg-success/[0.08] text-success", icon: CalendarCheck },
};

export function AiStatePill({ state, className }: { state: InteractionState; className?: string }) {
  const meta = AI_STATE_META[state] ?? { label: state, tone: "border-border text-muted", icon: Bot };
  const Icon = meta.icon;
  return (
    <span className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-medium", meta.tone, className)}>
      <Icon className="h-3 w-3" strokeWidth={2.25} aria-hidden="true" />
      {meta.label}
    </span>
  );
}
