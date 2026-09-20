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
      <div className="px-8 py-8">
        <header className="mb-6 flex items-center justify-between">
          <h1 className="font-display text-2xl text-foreground">Operations</h1>
          <button
            onClick={handleDetectDelays}
            disabled={detecting}
            className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
          >
            {detecting ? "Scanning..." : "Run delay detection"}
          </button>
        </header>

        {authLoading || loading ? (
          <Skeleton />
        ) : error ? (
          <div className="rounded-md border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : (
          <>
            <section className="mb-8 grid grid-cols-2 gap-4 sm:grid-cols-4">
              {dashboard &&
                METRIC_LABELS.map(({ key, label }) => (
                  <div key={key} className="rounded-lg border border-border bg-surface p-4">
                    <p className="text-2xl font-semibold">{dashboard[key]}</p>
                    <p className="mt-1 text-xs text-muted">{label}</p>
                  </div>
                ))}
            </section>

            <section>
              <h2 className="mb-3 text-sm font-medium text-muted">Needs your attention</h2>
              {exceptions.length === 0 ? (
                <EmptyState icon={CheckCircle2} title="No open exceptions." compact />
              ) : (
                <ul className="space-y-2">
                  {exceptions.map((e) => (
                    <li
                      key={e.id}
                      className="flex items-start justify-between rounded-md border border-border bg-surface px-4 py-3 text-sm"
                    >
                      <div>
                        <div className="flex items-center gap-2">
                          <Badge status={e.severity} className="text-[10px] uppercase">
                            {e.severity}
                          </Badge>
                          <span className="font-medium">{e.type}</span>
                        </div>
                        <p className="mt-1 text-muted">{e.description}</p>
                        {e.recommended_action && (
                          <p className="mt-1 text-xs text-muted-foreground">→ {e.recommended_action}</p>
                        )}
                      </div>
                      <button onClick={() => handleResolve(e.id)} className="text-xs text-muted underline hover:text-foreground">
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
