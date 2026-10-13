"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle } from "lucide-react";
import AppShell from "@/components/AppShell";
import { StatusSummary } from "@/components/ui/layout";
import { useAuth } from "@/lib/useAuth";
import { ApiError, OpsException, listExceptions, resolveException } from "@/lib/api";

import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
const STATUS_TABS = ["OPEN", "ACKNOWLEDGED", "RESOLVED"];

export default function ExceptionsPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [status, setStatus] = useState("OPEN");
  const [exceptions, setExceptions] = useState<OpsException[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const result = await listExceptions(token, status);
      setExceptions(result.exceptions);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load exceptions.");
    } finally {
      setLoading(false);
    }
  }, [token, status]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleResolve(id: string) {
    if (!token) return;
    try {
      await resolveException(token, id);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to resolve exception.");
    }
  }

  return (
    <AppShell user={user}>
      <div className="mx-auto w-full max-w-6xl px-5 py-8 sm:px-8 sm:py-12">
        <h1 className="font-display text-[2rem] font-semibold leading-[1.05] tracking-[-0.03em] text-foreground sm:text-[2.75rem] mb-3">Exceptions</h1>

        <div className="mb-4 flex gap-2">
          {STATUS_TABS.map((s) => (
            <button
              key={s}
              onClick={() => setStatus(s)}
              className={`klaros-filter-tab ${status === s ? "klaros-filter-tab-active" : "klaros-filter-tab-inactive"}`}
            >
              {s}
            </button>
          ))}
        </div>

        <StatusSummary rows={exceptions} noun="exceptions" />

        {authLoading || loading ? (
          <Skeleton />
        ) : error ? (
          <div className="rounded-xl border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : exceptions.length === 0 ? (
          <EmptyState icon={AlertTriangle} title={`No ${status.toLowerCase()} exceptions.`} />
        ) : (
          <div className="klaros-table-wrap">
            <table className="klaros-table">
              <thead>
                <tr>
                  <th className="px-5 py-4">Severity</th>
                  <th className="px-5 py-4">Type</th>
                  <th className="px-5 py-4">Entity</th>
                  <th className="px-5 py-4">Description</th>
                  <th className="px-5 py-4">Created</th>
                  <th className="px-5 py-4"></th>
                </tr>
              </thead>
              <tbody>
                {exceptions.map((e) => (
                  <tr key={e.id} className="border-t border-border">
                    <td className="px-5 py-4">
                      <Badge status={e.severity}>{e.severity}</Badge>
                    </td>
                    <td className="px-5 py-4 text-muted">{e.type}</td>
                    <td className="px-5 py-4 text-muted">
                      {e.entity_type}/{e.entity_id.slice(0, 8)}
                    </td>
                    <td className="px-5 py-4">{e.description}</td>
                    <td className="px-5 py-4 text-muted">{new Date(e.created_at).toLocaleString()}</td>
                    <td className="px-5 py-4">
                      {status === "OPEN" && (
                        <button onClick={() => handleResolve(e.id)} className="text-xs underline text-muted hover:text-foreground">
                          Resolve
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </AppShell>
  );
}
