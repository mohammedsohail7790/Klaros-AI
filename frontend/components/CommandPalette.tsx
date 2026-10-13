"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Command } from "cmdk";
import * as Dialog from "@radix-ui/react-dialog";
import { CornerDownLeft, Search } from "lucide-react";

export type PaletteItem = { href: string; label: string; group?: string; icon?: React.ComponentType<{ className?: string; strokeWidth?: number }> };

/** ⌘K / Ctrl+K quick switcher: jump to any page in the product by name. */
export default function CommandPalette({ items, open, onOpenChange }: { items: PaletteItem[]; open: boolean; onOpenChange: (open: boolean) => void }) {
  const router = useRouter();
  const [value, setValue] = useState("");

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        onOpenChange(!open);
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onOpenChange]);

  useEffect(() => {
    if (!open) setValue("");
  }, [open]);

  const groups = items.reduce<Record<string, PaletteItem[]>>((acc, item) => {
    (acc[item.group ?? "Pages"] ??= []).push(item);
    return acc;
  }, {});

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[60] bg-foreground/40 backdrop-blur-[2px] animate-[klaros-fade-in_0.12s_ease-out]" />
        <Dialog.Content
          aria-describedby={undefined}
          className="fixed left-1/2 top-[15vh] z-[60] w-[calc(100%-2rem)] max-w-xl -translate-x-1/2 overflow-hidden rounded-2xl border border-border bg-surface shadow-popover outline-none animate-[klaros-modal-in_0.16s_cubic-bezier(0.16,1,0.3,1)]"
        >
          <Dialog.Title className="sr-only">Jump to a page</Dialog.Title>
          <Command label="Jump to a page" loop>
            <div className="flex items-center gap-2.5 border-b border-border px-4">
              <Search className="h-4 w-4 shrink-0 text-muted-foreground" strokeWidth={2} />
              <Command.Input
                value={value}
                onValueChange={setValue}
                placeholder="Jump to a page…"
                className="h-12 w-full bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
              />
              <kbd className="rounded border border-border bg-surface-muted px-1.5 py-0.5 text-xs font-medium text-muted-foreground">ESC</kbd>
            </div>
            <Command.List className="max-h-[50vh] overflow-y-auto p-2">
              <Command.Empty className="px-3 py-8 text-center text-sm text-muted">No pages match “{value}”.</Command.Empty>
              {Object.entries(groups).map(([group, list]) => (
                <Command.Group key={group} heading={group} className="[&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:pt-3 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-muted-foreground">
                  {list.map((item) => {
                    const Icon = item.icon;
                    return (
                      <Command.Item
                        key={item.href}
                        value={`${item.label} ${group}`}
                        onSelect={() => {
                          onOpenChange(false);
                          router.push(item.href);
                        }}
                        className="flex cursor-pointer items-center gap-2.5 rounded-xl px-3 py-2 text-sm text-foreground aria-selected:bg-accent-soft aria-selected:text-accent-hover"
                      >
                        {Icon && <Icon className="h-4 w-4 shrink-0 text-muted-foreground" strokeWidth={2} />}
                        <span className="flex-1 truncate">{item.label}</span>
                        <CornerDownLeft className="h-3.5 w-3.5 opacity-0 aria-selected:opacity-100" aria-hidden="true" />
                      </Command.Item>
                    );
                  })}
                </Command.Group>
              ))}
            </Command.List>
          </Command>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
