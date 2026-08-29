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

function Bucket({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-neutral-800 p-4">
      <div className="text-xs text-neutral-500">{label}</div>
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
          <h1 className="text-xl font-semibold">Accounts Receivable</h1>
          <div className="flex gap-2">
            <button
              disabled={busy}
              onClick={handleDetectOverdue}
              className="rounded-md border border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-900 disabled:opacity-50"
            >
              Detect overdue
            </button>
            <button
              disabled={busy}
              onClick={handleExecuteDue}
              className="rounded-md border border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-900 disabled:opacity-50"
            >
              Execute due collections
            </button>
          </div>
        </div>

        {notice && (
          <div className="mb-4 rounded-md border border-emerald-900 bg-emerald-950/30 p-3 text-sm text-emerald-300">
            {notice}
          </div>
        )}

        {authLoading || loading ? (
          <p className="text-sm text-neutral-500">Loading...</p>
        ) : error ? (
          <div className="rounded-md border border-red-900 bg-red-950/30 p-4 text-sm text-red-300">
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

            <h2 className="mb-3 text-sm font-semibold text-neutral-400">Collection actions</h2>
            {actions.length === 0 ? (
              <p className="text-sm text-neutral-500">No collection actions scheduled.</p>
            ) : (
              <div className="overflow-x-auto rounded-lg border border-neutral-800">
                <table className="w-full text-left text-sm">
                  <thead className="bg-neutral-950 text-neutral-500">
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
                      <tr key={a.id} className="border-t border-neutral-900">
                        <td className="px-4 py-2">{a.invoice_number}</td>
                        <td className="px-4 py-2 text-neutral-400">{a.action_type}</td>
                        <td className="px-4 py-2 text-neutral-500">{new Date(a.scheduled_for).toLocaleString()}</td>
                        <td className="px-4 py-2">
                          <span className="rounded-full border border-neutral-700 px-2 py-0.5 text-xs">{a.status}</span>
                        </td>
                        <td className="px-4 py-2 text-neutral-500">{a.attempt}</td>
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
