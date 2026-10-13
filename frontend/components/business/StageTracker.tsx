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
  const doneCount = stages.filter((s) => s.state === "done").length;
  return (
    <nav aria-label="Business journey" className="relative mb-7 -mx-4 overflow-x-auto px-4 pb-2 sm:-mx-6 sm:px-6">
      <div className="mb-2 flex items-center justify-between text-xs text-muted">
        <span className="font-medium text-foreground">Your journey</span>
        <span>{doneCount} of {stages.length} steps done</span>
      </div>
      <div className="mb-3 h-1 overflow-hidden rounded-full bg-border" aria-hidden="true">
        <div className="h-full rounded-full bg-gradient-to-r from-accent to-violet-500 transition-all" style={{ width: `${(doneCount / Math.max(1, stages.length)) * 100}%` }} />
      </div>
      <ol className="flex min-w-max items-center gap-1 text-xs">
        {stages.map((s, i) => {
          const isActive = activeKey ? s.key === activeKey : s.state === "current";
          const body = (
            <span
              className={cn(
                "flex items-center gap-2 rounded-lg border px-3 py-1.5 font-medium transition-colors",
                s.state === "done" && "border-success/20 bg-success/[0.06] text-success hover:bg-success/10",
                s.state !== "done" && isActive && "border-accent bg-accent text-accent-foreground shadow-sm",
                s.state !== "done" && !isActive && "border-border bg-surface text-muted-foreground"
              )}
            >
              {s.state === "done" ? (
                <Check className="h-3.5 w-3.5" strokeWidth={3} aria-hidden="true" />
              ) : (
                <span className={cn("flex h-4 w-4 items-center justify-center rounded-full text-[10px]", isActive ? "bg-white/25" : "bg-surface-muted")}>{i + 1}</span>
              )}
              {s.label}
            </span>
          );
          return (
            <li key={s.key} className="flex items-center gap-1" aria-current={isActive ? "step" : undefined}>
              {s.state === "done" || isActive ? (
                <Link href={s.route} className="focus:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded-lg">
                  {body}
                  <span className="sr-only">{s.state === "done" ? " (completed)" : " (current)"}</span>
                </Link>
              ) : (
                body
              )}
              {i < stages.length - 1 && <span className="h-px w-3 bg-border-strong/60" aria-hidden="true" />}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
