"use client";

import { useState } from "react";
import { SlidersHorizontal } from "lucide-react";
import { cn } from "@/lib/cn";

/** On a phone the filters sit behind a button; from a laptop up they are always open beside the products. The links inside are rendered once, on the server. */
export function FilterToggle({ children, activeCount }: { children: React.ReactNode; activeCount: number }) {
  const [open, setOpen] = useState(false);
  return (
    <aside aria-label="Filters" className="lg:sticky lg:top-36 lg:self-start">
      <button type="button" aria-expanded={open} aria-controls="shop-filters" onClick={() => setOpen(!open)} className="flex h-11 w-full items-center justify-between rounded-full border border-[rgb(var(--color-border))] px-5 text-[16px] font-medium lg:hidden">
        <span className="inline-flex items-center gap-2"><SlidersHorizontal className="h-4 w-4" aria-hidden /> Filters</span>
        {activeCount > 0 && <span className="flex h-6 min-w-6 items-center justify-center rounded-full bg-[rgb(var(--color-brand))] px-1.5 text-[12px] font-bold text-[rgb(var(--color-background))]">{activeCount}</span>}
      </button>
      <div id="shop-filters" className={cn("mt-4 space-y-8 lg:mt-0 lg:block", open ? "block" : "hidden")}>{children}</div>
    </aside>
  );
}
