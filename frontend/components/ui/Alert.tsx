import { AlertTriangle, CheckCircle2, Info, XCircle, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/cn";

const VARIANTS: Record<string, { classes: string; icon: LucideIcon }> = {
  danger: {
    classes: "border-danger/20 bg-danger/[0.06] text-danger",
    icon: XCircle,
  },
  warning: {
    classes: "border-warning/25 bg-warning/[0.07] text-warning",
    icon: AlertTriangle,
  },
  success: {
    classes: "border-success/20 bg-success/[0.06] text-success",
    icon: CheckCircle2,
  },
  info: {
    classes: "border-accent/20 bg-accent-soft text-accent-hover",
    icon: Info,
  },
};

export function Alert({
  variant = "info",
  children,
  action,
  className,
}: {
  variant?: "danger" | "warning" | "success" | "info";
  children: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  const { classes, icon: Icon } = VARIANTS[variant];
  return (
    <div className={cn("flex items-start gap-2.5 rounded-lg border px-4 py-3 text-sm", classes, className)}>
      <Icon className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={2} />
      <div className="flex-1 leading-snug">
        {children}
        {action && <div className="mt-1.5">{action}</div>}
      </div>
    </div>
  );
}
