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
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/30 backdrop-blur-md animate-[klaros-fade-in_0.3s_ease-out]" />
        <div className="fixed inset-0 z-50 flex overflow-y-auto px-4 py-8">
          <Dialog.Content
            aria-describedby={undefined}
            className={cn(
              "m-auto w-full rounded-3xl bg-surface p-7 shadow-popover outline-none animate-[klaros-modal-in_0.4s_cubic-bezier(0.28,0.11,0.32,1)] sm:p-8",
              size === "lg" ? "max-w-2xl" : "max-w-md"
            )}
          >
            <div className="mb-5 flex items-center justify-between">
              <Dialog.Title className="font-display text-2xl font-semibold tracking-tight text-foreground">{title}</Dialog.Title>
              <Dialog.Close
                aria-label="Close"
                className="flex h-8 w-8 items-center justify-center rounded-full bg-surface-muted text-muted transition-colors hover:bg-border hover:text-foreground"
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
