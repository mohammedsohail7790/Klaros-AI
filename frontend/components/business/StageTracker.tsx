"use client";

import Link from "next/link";
import { Check } from "lucide-react";
import type { BuilderStage } from "@/lib/api";
import { cn } from "@/lib/cn";

/**
 * The journey strip shown at the top of every Business Builder screen:
 * Idea → Discovery → … → Operate. It only renders what the backend's
 * `stages` array says (done / current / todo) — completed stages link
 * back; stages not reached yet are not links, so a user can never jump
 * past a checkpoint from here.
 */
export function StageTracker({ stages, activeKey }: { stages: BuilderStage[]; activeKey?: string }) {
  return (
    <nav aria-label="Business journey" className="relative mb-6 -mx-4 overflow-x-auto px-4 pb-1 sm:-mx-6 sm:px-6">
      <ol className="flex min-w-max items-center gap-1 text-xs">
        {stages.map((s, i) => {
          const isActive = activeKey ? s.key === activeKey : s.state === "current";
          const body = (
            <span
              className={cn(
                "flex items-center gap-1.5 rounded-full border px-3 py-1.5 font-medium transition-colors",
                s.state === "done" && "border-success/25 bg-success/[0.07] text-success",
                s.state !== "done" && isActive && "border-accent bg-accent-soft text-accent-hover",
                s.state !== "done" && !isActive && "border-border text-muted-foreground",
                isActive && "ring-2 ring-accent/30"
              )}
            >
              {s.state === "done" ? (
                <Check className="h-3 w-3" strokeWidth={3} aria-hidden="true" />
              ) : (
                <span className="flex h-4 w-4 items-center justify-center rounded-full bg-surface-muted text-[10px]">{i + 1}</span>
              )}
              {s.label}
            </span>
          );
          return (
            <li key={s.key} className="flex items-center gap-1" aria-current={isActive ? "step" : undefined}>
              {s.state === "done" || isActive ? (
                <Link href={s.route} className="focus:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded-full">
                  {body}
                  <span className="sr-only">{s.state === "done" ? " (completed)" : " (current)"}</span>
                </Link>
              ) : (
                body
              )}
              {i < stages.length - 1 && <span className="h-px w-3 bg-border" aria-hidden="true" />}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
