"use client";

import { useCallback, useEffect, useState } from "react";
import { Heart } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, RetentionAnalytics, RetentionSummary, getRetentionAnalytics, getRetentionSummary } from "@/lib/api";
import { Skeleton } from "@/components/ui/Skeleton";
import { StatCard } from "@/components/ui/StatCard";
import { Alert } from "@/components/ui/Alert";
import { PageHeader } from "@/components/ui/PageHeader";

function Metric({ label, value, note }: { label: string; value: string | number | null; note: string }) {
  return (
    <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-1 text-xl font-semibold">{value !== null ? value : "INSUFFICIENT DATA"}</div>
      <div className="mt-1 text-xs text-muted-foreground">{note}</div>
    </div>
  );
}

export default function RetentionPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [summary, setSummary] = useState<RetentionSummary | null>(null);
  const [analytics, setAnalytics] = useState<RetentionAnalytics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [summaryResult, analyticsResult] = await Promise.all([getRetentionSummary(token), getRetentionAnalytics(token)]);
      setSummary(summaryResult);
      setAnalytics(analyticsResult);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load retention summary.");
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
        <PageHeader title="Retention & Referral" icon={Heart} />

        {authLoading || loading ? (
          <Skeleton stats={4} />
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
                RETENTION NEEDS ATTENTION — {summary.open_retention_exception_count} open retention exception(s).
              </Alert>
            )}
            <div className="mb-8 grid grid-cols-2 gap-4 md:grid-cols-4">
              <StatCard label="Active customers" value={summary.active_customers} tone="success" />
              <StatCard label="Repeat customers" value={summary.repeat_customers} />
              <StatCard label="At-risk customers" value={summary.at_risk_customers} tone={summary.at_risk_customers > 0 ? "danger" : "neutral"} />
              <StatCard label="Inactive customers" value={summary.inactive_customers} />
              <StatCard label="Retention opportunities" value={summary.retention_opportunities_open} tone="accent" />
              <StatCard label="Upcoming service reminders" value={summary.upcoming_service_reminders} />
              <StatCard label="Review requests sent" value={summary.review_requests_sent} />
              <StatCard label="Positive feedback" value={summary.positive_feedback_count} tone="success" />
              <StatCard label="Negative feedback" value={summary.negative_feedback_count} tone={summary.negative_feedback_count > 0 ? "danger" : "neutral"} />
              <StatCard label="Referral leads" value={summary.referral_leads} />
              <StatCard label="Referral conversions" value={summary.referral_conversions} tone="success" />
              <StatCard label="Referral revenue" value={`$${summary.referral_revenue}`} tone="accent" />
              <StatCard label="Repeat customer revenue" value={`$${summary.repeat_customer_revenue}`} tone="accent" />
            </div>

            {analytics && (
              <>
                <h2 className="mb-3 text-sm font-semibold text-muted">Analytics</h2>
                <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
                  <Metric label="Retention rate" value={analytics.retention_rate !== null ? `${analytics.retention_rate}%` : null} note={analytics.retention_rate_note} />
                  <Metric label="Repeat customer rate" value={analytics.repeat_customer_rate !== null ? `${analytics.repeat_customer_rate}%` : null} note={analytics.repeat_customer_rate_note} />
                  <Metric label="Reactivation rate" value={analytics.customer_reactivation_rate !== null ? `${analytics.customer_reactivation_rate}%` : null} note={analytics.customer_reactivation_rate_note} />
                  <Metric label="Average customer value" value={analytics.average_customer_value ? `$${analytics.average_customer_value}` : null} note={analytics.average_customer_value_note} />
                  <Metric label="Referral conversion rate" value={analytics.referral_conversion_rate !== null ? `${analytics.referral_conversion_rate}%` : null} note={analytics.referral_conversion_rate_note} />
                  <StatCard label="Revenue from repeat customers" value={`$${analytics.revenue_from_repeat_customers}`} tone="accent" />
                  <StatCard label="Revenue from referrals" value={`$${analytics.revenue_from_referrals}`} tone="accent" />
                </div>
              </>
            )}
          </>
        ) : null}
      </div>
    </AppShell>
  );
}
