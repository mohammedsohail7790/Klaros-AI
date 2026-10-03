"use client";

import { useEffect, useState } from "react";
import { ApiError, WorkflowDetail as Detail, getWorkflowDetail } from "@/lib/api";
import { actionLabel, titleCase, triggerLabel, when } from "@/lib/opsLabels";
import { cn } from "@/lib/cn";

const RUN_TONE: Record<string, string> = {
  COMPLETED: "text-success",
  FAILED: "text-danger font-medium",
  RUNNING: "text-accent-hover",
};

/** One workflow's real definition and its recent runs, step by step. Loaded only when opened. */
export function WorkflowDetailView({ token, id }: { token: string | null; id: string }) {
  const [d, setD] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await getWorkflowDetail(token, id);
        if (!cancelled) setD(r);
      } catch (err) {
        if (!cancelled) setError(err instanceof ApiError && err.status === 403 ? "You don't have permission to view this workflow." : "We couldn't load this workflow's runs just now.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token, id]);

  if (error) return <p role="alert" className="mt-3 text-sm text-danger">{error}</p>;
  if (!d) return <p className="mt-3 text-sm text-muted">Loading runs…</p>;
  return (
    <div className="mt-3 space-y-4 text-sm">
      <dl className="grid gap-x-6 gap-y-1 sm:grid-cols-3">
        <div><dt className="klaros-label">Starts when</dt><dd className="text-foreground">{triggerLabel(d.trigger_event)}</dd></div>
        <div><dt className="klaros-label">Runs</dt><dd className="text-foreground">{d.totals.runs} total · {d.totals.completed} completed · {d.totals.failed} failed</dd></div>
        <div><dt className="klaros-label">Steps</dt><dd className="text-foreground">{d.steps.length === 0 ? "None" : d.steps.map((s) => actionLabel(s.action)).join(" → ")}</dd></div>
      </dl>
      <div>
        <h4 className="klaros-label mb-1.5">Recent runs</h4>
        {d.runs.length === 0 ? (
          <p className="text-muted">This workflow hasn&apos;t run yet — it runs the next time its trigger happens.</p>
        ) : (
          <ul className="divide-y divide-border rounded-lg border border-border" aria-label="Recent runs">
            {d.runs.map((r) => (
              <li key={r.id} className="px-3 py-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className={cn("text-sm", RUN_TONE[r.status] ?? "text-foreground")}>{titleCase(r.status.toLowerCase())}</span>
                  <span className="text-xs text-muted-foreground">{when(r.at)}</span>
                </div>
                {r.steps.length > 0 && (
                  <ol className="mt-1 space-y-0.5 text-xs text-muted">
                    {r.steps.map((s) => (
                      <li key={s.index}>
                        {s.index + 1}. {actionLabel(s.action)} — {titleCase(s.status.toLowerCase())}
                        {s.error && <span className="text-danger"> · {s.error}</span>}
                      </li>
                    ))}
                  </ol>
                )}
                {r.error && <p className="mt-1 text-xs text-danger">{r.error}</p>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

/** The "View details" disclosure: nothing is requested until the user opens it. */
export function WorkflowDetailToggle({ token, id, name }: { token: string | null; id: string; name: string }) {
  const [open, setOpen] = useState(false);
  return (
    <details className="mt-3 border-t border-border pt-2" onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary className="cursor-pointer text-xs font-medium text-accent-hover hover:underline">
        View details<span className="sr-only"> of {name}</span>
      </summary>
      {open && <WorkflowDetailView token={token} id={id} />}
    </details>
  );
}
