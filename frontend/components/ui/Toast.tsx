"use client";

import { createContext, useCallback, useContext, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from "lucide-react";
import { cn } from "@/lib/cn";

type ToastVariant = "success" | "danger" | "warning" | "info";

interface Toast {
  id: number;
  variant: ToastVariant;
  message: string;
}

interface ToastContextValue {
  toast: {
    success: (message: string) => void;
    danger: (message: string) => void;
    warning: (message: string) => void;
    info: (message: string) => void;
  };
}

const ToastContext = createContext<ToastContextValue | null>(null);

const VARIANT_STYLES: Record<ToastVariant, { classes: string; icon: typeof CheckCircle2 }> = {
  success: { classes: "border-success/20 bg-surface text-success", icon: CheckCircle2 },
  danger: { classes: "border-danger/25 bg-surface text-danger", icon: XCircle },
  warning: { classes: "border-warning/25 bg-surface text-warning", icon: AlertTriangle },
  info: { classes: "border-accent/25 bg-surface text-accent-hover", icon: Info },
};

const AUTO_DISMISS_MS = 5000;

/**
 * App-wide toast notifications, mounted once in AppShell so every
 * dashboard page shares one stack instead of each page hand-rolling its
 * own inline "notice" banner. A transient confirmation ("Quote sent.",
 * "Spend recorded.") belongs here — it fades on its own and doesn't
 * compete for space with the page's real content. Persistent
 * information a page needs to keep showing (a generated customer link,
 * a validation error tied to a specific field) should stay inline as
 * before; toasts are for "this worked" acknowledgements only.
 */
export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(0);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const push = useCallback(
    (variant: ToastVariant, message: string) => {
      const id = nextId.current++;
      setToasts((prev) => [...prev, { id, variant, message }]);
      window.setTimeout(() => dismiss(id), AUTO_DISMISS_MS);
    },
    [dismiss]
  );

  const value: ToastContextValue = {
    toast: {
      success: (message) => push("success", message),
      danger: (message) => push("danger", message),
      warning: (message) => push("warning", message),
      info: (message) => push("info", message),
    },
  };

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        aria-live="polite"
        className="pointer-events-none fixed inset-x-0 bottom-0 z-[100] flex flex-col items-center gap-2 p-4 sm:items-end"
      >
        {toasts.map((t) => {
          const { classes, icon: Icon } = VARIANT_STYLES[t.variant];
          return (
            <div
              key={t.id}
              className={cn(
                "klaros-glass pointer-events-auto flex w-full max-w-sm items-start gap-2.5 rounded-xl border p-3.5 shadow-popover animate-[klaros-toast-in_0.2s_cubic-bezier(0.16,1,0.3,1)]",
                classes
              )}
            >
              <Icon className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={2} />
              <p className="flex-1 text-sm text-foreground">{t.message}</p>
              <button
                type="button"
                onClick={() => dismiss(t.id)}
                aria-label="Dismiss"
                className="shrink-0 text-muted-foreground transition-colors hover:text-foreground"
              >
                <X className="h-3.5 w-3.5" strokeWidth={2} />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within a ToastProvider");
  return ctx.toast;
}
