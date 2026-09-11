"use client";

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, FinanceSummary, getFinanceSummary } from "@/lib/api";

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-1 text-2xl font-semibold">{value}</div>
    </div>
  );
}

export default function FinancePage() {
  const { token, user, loading: authLoading } = useAuth();
  const [summary, setSummary] = useState<FinanceSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      setSummary(await getFinanceSummary(token));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load finance summary.");
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
        <h1 className="font-display text-2xl text-foreground mb-6">Finance</h1>

        {authLoading || loading ? (
          <p className="text-sm text-muted">Loading...</p>
        ) : error ? (
          <div className="rounded-md border border-red-200 bg-red-50/30 p-4 text-sm text-red-700">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : summary ? (
          <>
            {summary.needs_attention && (
              <div className="mb-6 rounded-md border border-amber-200 bg-amber-50/30 p-4 text-sm text-amber-700">
                FINANCE NEEDS ATTENTION — {summary.overdue_invoice_count} overdue invoice(s),{" "}
                {summary.open_finance_exception_count} open finance exception(s).
              </div>
            )}
            <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
              <Stat label="Total AR (outstanding)" value={`$${summary.total_ar}`} />
              <Stat label="Overdue invoices" value={summary.overdue_invoice_count} />
              <Stat label="Pending approval" value={summary.pending_approval_invoice_count} />
              <Stat label="Draft invoices" value={summary.draft_invoice_count} />
              <Stat label="Total paid (all time)" value={`$${summary.total_paid}`} />
              <Stat label="Open finance exceptions" value={summary.open_finance_exception_count} />
            </div>
          </>
        ) : null}
      </div>
    </AppShell>
  );
}
