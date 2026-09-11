"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { AIActivityRow, ApiError, listAIActivity } from "@/lib/api";

const RESULT_COLOR: Record<string, string> = {
  success: "border-emerald-200 text-emerald-700",
  failure: "border-red-200 text-red-700",
};

const ACTOR_COLOR: Record<string, string> = {
  AI: "border-purple-200 text-purple-700",
  USER: "border-border-strong text-muted",
  SYSTEM: "border-blue-200 text-blue-700",
};

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
          <h1 className="text-xl font-semibold">AI Activity</h1>
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
          <p className="text-sm text-muted">Loading...</p>
        ) : error ? (
          <div className="rounded-md border border-red-200 bg-red-50/30 p-4 text-sm text-red-700">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">Retry</button>
          </div>
        ) : !rows || rows.length === 0 ? (
          <p className="text-sm text-muted">No AI activity recorded yet — generate a Morning Brief to see it here.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-border">
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
                      <span className={`rounded-full border px-2 py-0.5 text-[10px] ${ACTOR_COLOR[r.actor_type] ?? "border-border-strong"}`}>
                        {r.actor_type}
                      </span>
                    </td>
                    <td className="px-3 py-2">{r.action}</td>
                    <td className="px-3 py-2 text-muted">{r.tool ?? "—"}</td>
                    <td className="px-3 py-2 text-muted">
                      {r.entity_type ? `${r.entity_type}${r.entity_id ? ` #${r.entity_id.slice(0, 8)}` : ""}` : "—"}
                    </td>
                    <td className="px-3 py-2">
                      <span className={`rounded-full border px-2 py-0.5 text-[10px] ${RESULT_COLOR[r.result] ?? "border-border-strong"}`}>
                        {r.result}
                      </span>
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
