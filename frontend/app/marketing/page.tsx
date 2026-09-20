"use client";

import { useCallback, useEffect, useState } from "react";
import { Megaphone } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { AdsProviderStatus, ApiError, MarketingSummary, getAdsProviderStatus, getMarketingSummary } from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { StatCard } from "@/components/ui/StatCard";
import { Alert } from "@/components/ui/Alert";
import { PageHeader } from "@/components/ui/PageHeader";
import { BarBreakdown } from "@/components/ui/Chart";

export default function MarketingPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [summary, setSummary] = useState<MarketingSummary | null>(null);
  const [providers, setProviders] = useState<AdsProviderStatus[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [summaryResult, providersResult] = await Promise.all([getMarketingSummary(token), getAdsProviderStatus(token)]);
      setSummary(summaryResult);
      setProviders(providersResult.providers);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load marketing summary.");
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
        <PageHeader title="Marketing & Demand Generation" icon={Megaphone} />

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
                MARKETING NEEDS ATTENTION — {summary.open_marketing_exception_count} open marketing exception(s).
              </Alert>
            )}
            <div className="mb-8 grid grid-cols-2 gap-4 md:grid-cols-4">
              <StatCard label="Marketing spend" value={`$${summary.marketing_spend}`} tone="accent" />
              <StatCard label="Leads" value={summary.leads} />
              <StatCard label="Qualified leads" value={summary.qualified_leads} />
              <StatCard label="Appointments booked" value={summary.appointments_booked} />
              <StatCard label="Jobs won" value={summary.jobs_won} tone="success" />
              <StatCard label="Revenue attributed" value={`$${summary.revenue}`} tone="accent" />
              <StatCard label="Collected revenue" value={`$${summary.collected_revenue}`} tone="success" />
              <StatCard
                label="CAC"
                value={summary.cac ? `$${summary.cac}` : "Insufficient data"}
                note={summary.cac ? null : summary.cac_note}
              />
              <StatCard
                label="ROAS"
                value={summary.roas ? `${summary.roas}x` : "Insufficient data"}
                note={summary.roas ? null : summary.roas_note}
              />
              <StatCard
                label="Conversion rate"
                value={summary.conversion_rate_pct !== null ? `${summary.conversion_rate_pct}%` : "No leads yet"}
              />
              <StatCard label="Campaigns" value={summary.campaign_count} />
            </div>

            {summary.leads > 0 && (
              <div className="mb-8 klaros-card p-5">
                <div className="mb-3 text-sm font-medium text-muted">Funnel: leads → qualified → booked → won</div>
                <BarBreakdown
                  bars={[
                    { label: "Leads", value: summary.leads, tone: "accent" },
                    { label: "Qualified", value: summary.qualified_leads, tone: "accent" },
                    { label: "Booked", value: summary.appointments_booked, tone: "accent" },
                    { label: "Won", value: summary.jobs_won, tone: "success" },
                  ]}
                />
              </div>
            )}

            <h2 className="mb-3 text-sm font-semibold text-muted">Paid ads integration status</h2>
            <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
              {providers.map((p) => (
                <div key={p.provider} className="klaros-card p-4">
                  <div className="text-sm font-medium">{p.provider.replace(/_/g, " ")}</div>
                  <Badge status={p.status} className="mt-1">
                    {p.status}
                  </Badge>
                  <div className="mt-2 text-xs text-muted">{p.detail}</div>
                </div>
              ))}
            </div>
          </>
        ) : null}
      </div>
    </AppShell>
  );
}
