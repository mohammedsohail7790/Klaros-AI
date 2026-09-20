"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { BrainCircuit } from "lucide-react";
import AppShell from "@/components/AppShell";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { useAuth } from "@/lib/useAuth";
import { AIActivityRow, ApiError, listAIActivity } from "@/lib/api";

export default function AIActivityPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [rows, setRows] = useState<AIActivityRow[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const result = await listAIActivity(token);
      setRows(result.rows);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load AI activity.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <div className="mb-2 flex items-center justify-between">
          <h1 className="font-display text-2xl text-foreground">AI Activity</h1>
          <button
            onClick={load}
            className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted"
          >
            Refresh
          </button>
        </div>
        <p className="mb-6 text-xs text-muted">
          Every AI-actor tool call (Morning Brief insight generation) and everything it led to through the
          approval system — the same audit log every other action writes to, filtered, not a separate store.
        </p>

        {authLoading || loading ? (
          <Skeleton />
        ) : error ? (
          <div className="rounded-md border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">Retry</button>
          </div>
        ) : !rows || rows.length === 0 ? (
          <EmptyState icon={BrainCircuit} title="No AI activity recorded yet — generate a Morning Brief to see it here." />
        ) : (
          <div className="klaros-table-wrap">
            <table className="w-full text-sm">
              <thead className="bg-surface text-left text-xs text-muted">
                <tr>
                  <th className="px-3 py-2">When</th>
                  <th className="px-3 py-2">Actor</th>
                  <th className="px-3 py-2">Action</th>
                  <th className="px-3 py-2">Tool</th>
                  <th className="px-3 py-2">Entity</th>
                  <th className="px-3 py-2">Result</th>
                  <th className="px-3 py-2">Approval</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className="border-t border-border">
                    <td className="px-3 py-2 text-muted">{new Date(r.created_at).toLocaleString()}</td>
                    <td className="px-3 py-2">
                      <Badge status={r.actor_type} className="text-[10px]">
                        {r.actor_type}
                      </Badge>
                    </td>
                    <td className="px-3 py-2">{r.action}</td>
                    <td className="px-3 py-2 text-muted">{r.tool ?? "—"}</td>
                    <td className="px-3 py-2 text-muted">
                      {r.entity_type ? `${r.entity_type}${r.entity_id ? ` #${r.entity_id.slice(0, 8)}` : ""}` : "—"}
                    </td>
                    <td className="px-3 py-2">
                      <Badge status={r.result} className="text-[10px]">
                        {r.result}
                      </Badge>
                    </td>
                    <td className="px-3 py-2">
                      {r.approval_id ? (
                        <Link href="/approvals" className="text-xs underline">
                          view
                        </Link>
                      ) : (
                        "—"
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
