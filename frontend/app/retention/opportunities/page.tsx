"use client";

import { useCallback, useEffect, useState } from "react";
import { TrendingUp } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, RetentionOpportunityRow, listRetentionOpportunities, updateOpportunityStatus } from "@/lib/api";

import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
const STATUS_TABS = ["OPEN", "CONTACTED", "CONVERTED", "DISMISSED", "EXPIRED"];

export default function OpportunitiesPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [status, setStatus] = useState("OPEN");
  const [opportunities, setOpportunities] = useState<RetentionOpportunityRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      setOpportunities((await listRetentionOpportunities(token, status)).opportunities);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load opportunities.");
    } finally {
      setLoading(false);
    }
  }, [token, status]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleUpdate(id: string, newStatus: string) {
    if (!token) return;
    setBusy(true);
    try {
      await updateOpportunityStatus(token, id, newStatus);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to update opportunity.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <h1 className="font-display text-2xl text-foreground mb-6">Retention Opportunities</h1>

        <div className="mb-4 flex flex-wrap gap-2">
          {STATUS_TABS.map((s) => (
            <button
              key={s}
              onClick={() => setStatus(s)}
              className={`rounded-full border px-3 py-1 text-xs ${status === s ? "border-foreground bg-surface text-foreground" : "border-border-strong text-muted"}`}
            >
              {s}
            </button>
          ))}
        </div>

        {authLoading || loading ? (
          <Skeleton />
        ) : error ? (
          <div className="rounded-md border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">Retry</button>
          </div>
        ) : opportunities.length === 0 ? (
          <EmptyState icon={TrendingUp} title={`No ${status.toLowerCase()} opportunities.`} />
        ) : (
          <div className="klaros-table-wrap">
            <table className="klaros-table">
              <thead className="bg-surface text-muted">
                <tr>
                  <th className="px-4 py-2">Type</th>
                  <th className="px-4 py-2">Reason</th>
                  <th className="px-4 py-2">Priority</th>
                  <th className="px-4 py-2">Detected</th>
                  <th className="px-4 py-2">Recommended action</th>
                  <th className="px-4 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {opportunities.map((o) => (
                  <tr key={o.id} className="border-t border-border">
                    <td className="px-4 py-2 text-muted">{o.type}</td>
                    <td className="max-w-sm px-4 py-2 text-muted">{o.reason}</td>
                    <td className="px-4 py-2">
                      <Badge status={o.priority}>{o.priority}</Badge>
                    </td>
                    <td className="px-4 py-2 text-muted">{new Date(o.detected_at).toLocaleDateString()}</td>
                    <td className="px-4 py-2 text-muted">{o.recommended_action}</td>
                    <td className="px-4 py-2 space-x-2">
                      {status === "OPEN" && (
                        <>
                          <button disabled={busy} onClick={() => handleUpdate(o.id, "CONTACTED")} className="text-xs underline text-muted hover:text-foreground">Contact</button>
                          <button disabled={busy} onClick={() => handleUpdate(o.id, "CONVERTED")} className="text-xs underline text-success hover:text-foreground">Convert</button>
                          <button disabled={busy} onClick={() => handleUpdate(o.id, "DISMISSED")} className="text-xs underline text-danger hover:text-foreground">Dismiss</button>
                        </>
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
