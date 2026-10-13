"use client";

import { useCallback, useEffect, useState } from "react";
import { CheckCircle2 } from "lucide-react";
import AppShell from "@/components/AppShell";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  OperationsDashboard,
  OpsException,
  detectDelays,
  getOperationsDashboard,
  listExceptions,
  resolveException,
} from "@/lib/api";

const METRIC_LABELS: { key: keyof OperationsDashboard; label: string }[] = [
  { key: "jobs_today", label: "Jobs today" },
  { key: "unassigned_jobs", label: "Unassigned" },
  { key: "at_risk_jobs", label: "At risk" },
  { key: "blocked_jobs", label: "Blocked" },
  { key: "in_progress_jobs", label: "In progress" },
  { key: "qa_pending_jobs", label: "Awaiting QA" },
  { key: "completed_today", label: "Completed today" },
  { key: "open_exceptions", label: "Open exceptions" },
];

export default function OperationsPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [dashboard, setDashboard] = useState<OperationsDashboard | null>(null);
  const [exceptions, setExceptions] = useState<OpsException[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [detecting, setDetecting] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [dash, exc] = await Promise.all([getOperationsDashboard(token), listExceptions(token)]);
      setDashboard(dash);
      setExceptions(exc.exceptions);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load operations dashboard.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleDetectDelays() {
    if (!token) return;
    setDetecting(true);
    try {
      await detectDelays(token);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to run delay detection.");
    } finally {
      setDetecting(false);
    }
  }

  async function handleResolve(exceptionId: string) {
    if (!token) return;
    try {
      await resolveException(token, exceptionId);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to resolve exception.");
    }
  }

  return (
    <AppShell user={user}>
      <div className="mx-auto w-full max-w-6xl px-5 py-8 sm:px-8 sm:py-12">
        <header className="mb-10 flex flex-wrap items-end justify-between gap-4">
          <h1 className="font-display text-[2rem] font-semibold leading-[1.05] tracking-[-0.03em] text-foreground sm:text-[2.75rem]">Operations</h1>
          <button
            onClick={handleDetectDelays}
            disabled={detecting}
            className="klaros-btn-primary disabled:opacity-50"
          >
            {detecting ? "Scanning..." : "Run delay detection"}
          </button>
        </header>

        {authLoading || loading ? (
          <Skeleton />
        ) : error ? (
          <div className="rounded-xl border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : (
          <>
            <section className="mb-12 grid grid-cols-2 gap-3 sm:grid-cols-4">
              {dashboard &&
                METRIC_LABELS.map(({ key, label }) => (
                  <div key={key} className="rounded-3xl bg-surface-muted p-6">
                    <p className="text-[13px] font-medium text-muted">{label}</p>
                    <p className="mt-4 font-display text-[2.5rem] font-semibold leading-none tracking-[-0.035em] text-foreground">{dashboard[key]}</p>
                  </div>
                ))}
            </section>

            <section>
              <h2 className="mb-4 font-display text-[1.5rem] font-semibold tracking-[-0.02em] text-foreground">Needs your attention</h2>
              {exceptions.length === 0 ? (
                <EmptyState icon={CheckCircle2} title="No open exceptions." compact />
              ) : (
                <ul className="space-y-3">
                  {exceptions.map((e) => (
                    <li
                      key={e.id}
                      className="flex flex-wrap items-start justify-between gap-4 rounded-3xl bg-surface-muted p-6"
                    >
                      <div>
                        <div className="flex items-center gap-2">
                          <Badge status={e.severity} className="text-xs uppercase">
                            {e.severity}
                          </Badge>
                          <span className="text-[17px] font-semibold tracking-tight">{e.type}</span>
                        </div>
                        <p className="mt-2 text-[15px] leading-snug text-muted">{e.description}</p>
                        {e.recommended_action && (
                          <p className="mt-1 text-xs text-muted-foreground">→ {e.recommended_action}</p>
                        )}
                      </div>
                      <button onClick={() => handleResolve(e.id)} className="rounded-full bg-surface px-4 py-1.5 text-[13px] font-medium text-foreground hover:bg-border">
                        Resolve
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        )}
      </div>
    </AppShell>
  );
}
