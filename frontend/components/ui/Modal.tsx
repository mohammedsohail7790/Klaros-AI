"use client";

import { useEffect } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/cn";

/**
 * The shared shell for every "New X" / confirmation overlay in the
 * dashboard — backdrop, panel chrome (radius, shadow, entrance), a
 * consistent title row with a close button, and Escape-to-close.
 * Previously each of ~11 files hand-rolled its own `fixed inset-0`
 * overlay with slightly different backdrop opacity, no entrance
 * animation, and a plain `<h2>` for a title — this replaces all of that
 * with one component so a form's own markup is the only thing that
 * differs between modals.
 */
export function Modal({
  title,
  onClose,
  children,
  size = "md",
}: {
  title: React.ReactNode;
  onClose: () => void;
  children: React.ReactNode;
  /** md: forms (400-480px). lg: wider content like an import-mapping table. */
  size?: "md" | "lg";
}) {
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-foreground/20 px-4 py-8 backdrop-blur-sm animate-[klaros-fade-in_0.15s_ease-out]"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className={cn(
          "klaros-glass w-full rounded-2xl p-6 animate-[klaros-modal-in_0.18s_cubic-bezier(0.16,1,0.3,1)]",
          size === "lg" ? "max-w-2xl" : "max-w-md"
        )}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-display text-xl text-foreground">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="flex h-7 w-7 items-center justify-center rounded-lg text-muted transition-colors hover:bg-surface-muted hover:text-foreground"
          >
            <X className="h-4 w-4" strokeWidth={2} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
