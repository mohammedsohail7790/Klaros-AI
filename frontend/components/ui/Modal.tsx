"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { cn } from "@/lib/cn";

/**
 * The shared shell for every "New X" / confirmation overlay. Built on Radix
 * Dialog, so focus is trapped and restored, Escape and outside-click close it,
 * and screen readers get a proper dialog with a title.
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
  return (
    <Dialog.Root open onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-foreground/40 backdrop-blur-[2px] animate-[klaros-fade-in_0.15s_ease-out]" />
        <div className="fixed inset-0 z-50 flex overflow-y-auto px-4 py-8">
          <Dialog.Content
            aria-describedby={undefined}
            className={cn(
              "m-auto w-full rounded-2xl border border-border bg-surface p-6 shadow-popover outline-none animate-[klaros-modal-in_0.18s_cubic-bezier(0.16,1,0.3,1)]",
              size === "lg" ? "max-w-2xl" : "max-w-md"
            )}
          >
            <div className="mb-4 flex items-center justify-between">
              <Dialog.Title className="text-lg font-semibold tracking-tight text-foreground">{title}</Dialog.Title>
              <Dialog.Close
                aria-label="Close"
                className="flex h-8 w-8 items-center justify-center rounded-lg text-muted transition-colors hover:bg-surface-muted hover:text-foreground"
              >
                <X className="h-4 w-4" strokeWidth={2} />
              </Dialog.Close>
            </div>
            {children}
          </Dialog.Content>
        </div>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
