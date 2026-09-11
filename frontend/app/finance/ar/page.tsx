"use client";

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  AgingSummary,
  ApiError,
  CollectionActionRow,
  detectOverdueInvoices,
  executeDueCollections,
  getARAging,
  listCollectionActions,
} from "@/lib/api";

import { Badge } from "@/components/ui/Badge";
function Bucket({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-1 text-xl font-semibold">${value}</div>
    </div>
  );
}

export default function ARPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [aging, setAging] = useState<AgingSummary | null>(null);
  const [actions, setActions] = useState<CollectionActionRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [agingResult, actionsResult] = await Promise.all([getARAging(token), listCollectionActions(token)]);
      setAging(agingResult);
      setActions(actionsResult.collection_actions);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load AR data.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleDetectOverdue() {
    if (!token) return;
    setBusy(true);
    setNotice(null);
    try {
      const result = await detectOverdueInvoices(token);
      setNotice(`${result.newly_overdue_invoice_ids.length} invoice(s) newly marked OVERDUE.`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to detect overdue invoices.");
    } finally {
      setBusy(false);
    }
  }

  async function handleExecuteDue() {
    if (!token) return;
    setBusy(true);
    setNotice(null);
    try {
      const result = await executeDueCollections(token);
      setNotice(`${result.executed_action_ids.length} collection action(s) executed.`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to execute collection actions.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <div className="mb-6 flex items-center justify-between">
          <h1 className="font-display text-2xl text-foreground">Accounts Receivable</h1>
          <div className="flex gap-2">
            <button
              disabled={busy}
              onClick={handleDetectOverdue}
              className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
            >
              Detect overdue
            </button>
            <button
              disabled={busy}
              onClick={handleExecuteDue}
              className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
            >
              Execute due collections
            </button>
          </div>
        </div>

        {notice && (
          <div className="mb-4 rounded-md border border-emerald-200 bg-emerald-50/30 p-3 text-sm text-emerald-700">
            {notice}
          </div>
        )}

        {authLoading || loading ? (
          <p className="text-sm text-muted">Loading...</p>
        ) : error ? (
          <div className="rounded-md border border-red-200 bg-red-50/30 p-4 text-sm text-red-700">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : (
          <>
            {aging && (
              <div className="mb-8 grid grid-cols-2 gap-4 md:grid-cols-5">
                <Bucket label="Current" value={aging.current} />
                <Bucket label="1-30 days" value={aging.days_1_30} />
                <Bucket label="31-60 days" value={aging.days_31_60} />
                <Bucket label="61-90 days" value={aging.days_61_90} />
                <Bucket label="90+ days" value={aging.days_90_plus} />
              </div>
            )}

            <h2 className="mb-3 text-sm font-semibold text-muted">Collection actions</h2>
            {actions.length === 0 ? (
              <p className="text-sm text-muted">No collection actions scheduled.</p>
            ) : (
              <div className="klaros-table-wrap">
                <table className="klaros-table">
                  <thead className="bg-surface text-muted">
                    <tr>
                      <th className="px-4 py-2">Invoice</th>
                      <th className="px-4 py-2">Action</th>
                      <th className="px-4 py-2">Scheduled for</th>
                      <th className="px-4 py-2">Status</th>
                      <th className="px-4 py-2">Attempt</th>
                    </tr>
                  </thead>
                  <tbody>
                    {actions.map((a) => (
                      <tr key={a.id} className="border-t border-border">
                        <td className="px-4 py-2">{a.invoice_number}</td>
                        <td className="px-4 py-2 text-muted">{a.action_type}</td>
                        <td className="px-4 py-2 text-muted">{new Date(a.scheduled_for).toLocaleString()}</td>
                        <td className="px-4 py-2">
                          <Badge status={a.status}>{a.status}</Badge>
                        </td>
                        <td className="px-4 py-2 text-muted">{a.attempt}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
    </AppShell>
  );
}
