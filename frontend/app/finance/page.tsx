"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertCircle, Banknote, Clock, FileClock, FileText, Wallet } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, FinanceSummary, getFinanceSummary } from "@/lib/api";
import { Skeleton } from "@/components/ui/Skeleton";
import { StatCard } from "@/components/ui/StatCard";
import { Alert } from "@/components/ui/Alert";
import { PageHeader } from "@/components/ui/PageHeader";

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
        <PageHeader title="Finance" icon={Banknote} />

        {authLoading || loading ? (
          <Skeleton stats={6} />
        ) : error ? (
          <Alert variant="danger">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </Alert>
        ) : summary ? (
          <>
            {summary.needs_attention && (
              <Alert variant="warning" className="mb-6">
                FINANCE NEEDS ATTENTION — {summary.overdue_invoice_count} overdue invoice(s),{" "}
                {summary.open_finance_exception_count} open finance exception(s).
              </Alert>
            )}
            <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
              <StatCard label="Total AR (outstanding)" value={`$${summary.total_ar}`} icon={Wallet} tone="accent" />
              <StatCard
                label="Overdue invoices"
                value={summary.overdue_invoice_count}
                icon={AlertCircle}
                tone={summary.overdue_invoice_count > 0 ? "danger" : "neutral"}
              />
              <StatCard label="Pending approval" value={summary.pending_approval_invoice_count} icon={Clock} tone="warning" />
              <StatCard label="Draft invoices" value={summary.draft_invoice_count} icon={FileClock} />
              <StatCard label="Total paid (all time)" value={`$${summary.total_paid}`} icon={Banknote} tone="success" />
              <StatCard
                label="Open finance exceptions"
                value={summary.open_finance_exception_count}
                icon={FileText}
                tone={summary.open_finance_exception_count > 0 ? "danger" : "neutral"}
              />
            </div>
          </>
        ) : null}
      </div>
    </AppShell>
  );
}
