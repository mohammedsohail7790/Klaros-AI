"use client";

import { createContext, useContext, useMemo } from "react";
import { Toaster, toast as sonner } from "sonner";

interface ToastContextValue {
  toast: {
    success: (message: string) => void;
    danger: (message: string) => void;
    warning: (message: string) => void;
    info: (message: string) => void;
  };
}

const ToastContext = createContext<ToastContextValue | null>(null);

/**
 * App-wide toast notifications (Sonner), mounted once at the root. A transient
 * confirmation ("Quote sent.") belongs here; anything a user must keep seeing
 * stays inline on the page.
 */
export function ToastProvider({ children }: { children: React.ReactNode }) {
  const value = useMemo<ToastContextValue>(
    () => ({
      toast: {
        success: (m) => void sonner.success(m),
        danger: (m) => void sonner.error(m),
        warning: (m) => void sonner.warning(m),
        info: (m) => void sonner.message(m),
      },
    }),
    []
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      <Toaster
        position="bottom-right"
        richColors
        closeButton
        toastOptions={{
          classNames: {
            toast: "!rounded-xl !border-border !shadow-popover !font-sans",
          },
        }}
      />
    </ToastContext.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within a ToastProvider");
  return ctx.toast;
}
