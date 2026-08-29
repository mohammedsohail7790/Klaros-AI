"use client";

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, RetentionOpportunityRow, listRetentionOpportunities, updateOpportunityStatus } from "@/lib/api";

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
        <h1 className="mb-6 text-xl font-semibold">Retention Opportunities</h1>

        <div className="mb-4 flex flex-wrap gap-2">
          {STATUS_TABS.map((s) => (
            <button
              key={s}
              onClick={() => setStatus(s)}
              className={`rounded-full border px-3 py-1 text-xs ${status === s ? "border-white bg-white text-black" : "border-neutral-700 text-neutral-400"}`}
            >
              {s}
            </button>
          ))}
        </div>

        {authLoading || loading ? (
          <p className="text-sm text-neutral-500">Loading...</p>
        ) : error ? (
          <div className="rounded-md border border-red-900 bg-red-950/30 p-4 text-sm text-red-300">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">Retry</button>
          </div>
        ) : opportunities.length === 0 ? (
          <p className="text-sm text-neutral-500">No {status.toLowerCase()} opportunities.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-neutral-800">
            <table className="w-full text-left text-sm">
              <thead className="bg-neutral-950 text-neutral-500">
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
                  <tr key={o.id} className="border-t border-neutral-900">
                    <td className="px-4 py-2 text-neutral-300">{o.type}</td>
                    <td className="max-w-sm px-4 py-2 text-neutral-400">{o.reason}</td>
                    <td className="px-4 py-2">
                      <span className="rounded-full border border-neutral-700 px-2 py-0.5 text-xs">{o.priority}</span>
                    </td>
                    <td className="px-4 py-2 text-neutral-500">{new Date(o.detected_at).toLocaleDateString()}</td>
                    <td className="px-4 py-2 text-neutral-500">{o.recommended_action}</td>
                    <td className="px-4 py-2 space-x-2">
                      {status === "OPEN" && (
                        <>
                          <button disabled={busy} onClick={() => handleUpdate(o.id, "CONTACTED")} className="text-xs underline text-neutral-400 hover:text-white">Contact</button>
                          <button disabled={busy} onClick={() => handleUpdate(o.id, "CONVERTED")} className="text-xs underline text-emerald-400 hover:text-white">Convert</button>
                          <button disabled={busy} onClick={() => handleUpdate(o.id, "DISMISSED")} className="text-xs underline text-red-400 hover:text-white">Dismiss</button>
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
