"use client";

import { useCallback, useEffect, useState } from "react";
import { Landmark } from "lucide-react";
import AppShell from "@/components/AppShell";
import { EmptyState } from "@/components/ui/EmptyState";
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
import { Skeleton } from "@/components/ui/Skeleton";
import { StatCard } from "@/components/ui/StatCard";
import { Alert } from "@/components/ui/Alert";
import { PageHeader } from "@/components/ui/PageHeader";
import { BarBreakdown } from "@/components/ui/Chart";
import { useToast } from "@/components/ui/Toast";

export default function ARPage() {
  const toast = useToast();
  const { token, user, loading: authLoading } = useAuth();
  const [aging, setAging] = useState<AgingSummary | null>(null);
  const [actions, setActions] = useState<CollectionActionRow[]>([]);
  const [error, setError] = useState<string | null>(null);
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
    try {
      const result = await detectOverdueInvoices(token);
      toast.success(`${result.newly_overdue_invoice_ids.length} invoice(s) newly marked OVERDUE.`);
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
    try {
      const result = await executeDueCollections(token);
      toast.success(`${result.executed_action_ids.length} collection action(s) executed.`);
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
        <PageHeader
          title="Accounts Receivable"
          icon={Landmark}
          actions={
            <>
              <button
                disabled={busy}
                onClick={handleDetectOverdue}
                className="klaros-btn-secondary disabled:opacity-50"
              >
                Detect overdue
              </button>
              <button
                disabled={busy}
                onClick={handleExecuteDue}
                className="klaros-btn-secondary disabled:opacity-50"
              >
                Execute due collections
              </button>
            </>
          }
        />

        {authLoading || loading ? (
          <Skeleton stats={5} />
        ) : error ? (
          <Alert variant="danger">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </Alert>
        ) : (
          <>
            {aging && (
              <div className="mb-8 grid grid-cols-2 gap-4 md:grid-cols-5">
                <StatCard label="Current" value={`$${aging.current}`} />
                <StatCard label="1-30 days" value={`$${aging.days_1_30}`} />
                <StatCard label="31-60 days" value={`$${aging.days_31_60}`} tone="warning" />
                <StatCard label="61-90 days" value={`$${aging.days_61_90}`} tone="warning" />
                <StatCard label="90+ days" value={`$${aging.days_90_plus}`} tone="danger" />
              </div>
            )}

            {aging && Number(aging.total) > 0 && (
              <div className="mb-8 klaros-card p-5">
                <div className="mb-3 text-sm font-medium text-muted">Aging breakdown</div>
                <BarBreakdown
                  formatValue={(v) => `$${v.toLocaleString()}`}
                  bars={[
                    { label: "Current", value: Number(aging.current) },
                    { label: "1-30 days", value: Number(aging.days_1_30) },
                    { label: "31-60 days", value: Number(aging.days_31_60), tone: "warning" },
                    { label: "61-90 days", value: Number(aging.days_61_90), tone: "warning" },
                    { label: "90+ days", value: Number(aging.days_90_plus), tone: "danger" },
                  ]}
                />
              </div>
            )}

            <h2 className="mb-3 text-sm font-semibold text-muted">Collection actions</h2>
            {actions.length === 0 ? (
              <EmptyState icon={Landmark} title="No collection actions scheduled." />
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
