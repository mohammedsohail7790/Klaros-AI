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
import { plainDetail, statusLabel } from "@/lib/integrationLabels";

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
      <div className="mx-auto w-full max-w-6xl px-5 py-8 sm:px-8 sm:py-12">
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
              <StatCard label="All enquiries" value={summary.total_leads} note={summary.total_leads > 0 ? `${summary.qualified_total} qualified` : null} />
              <StatCard label="From campaigns" value={summary.leads} note={summary.unattributed_leads > 0 ? `${summary.unattributed_leads} not from a campaign` : null} />
              <StatCard label="Qualified from campaigns" value={summary.qualified_leads} />
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

            {summary.total_leads > 0 && (
              <div className="mb-8 klaros-card p-5">
                <div className="mb-3 text-sm font-medium text-muted">Where your enquiries come from</div>
                <ul className="flex flex-wrap gap-x-8 gap-y-2 text-[15px] text-foreground">
                  {Object.entries(summary.leads_by_source).sort((a, b) => b[1] - a[1]).map(([source, n]) => (
                    <li key={source}><span className="font-medium">{n}</span> {source.toLowerCase().replace(/_/g, " ")}</li>
                  ))}
                </ul>
              </div>
            )}

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
                    {statusLabel(p.status)}
                  </Badge>
                  <div className="mt-2 text-xs text-muted">{p.status === "CONNECTED" ? plainDetail(p.status, p.detail) : `${plainDetail(p.status, p.detail)} No campaign data has been synced.`}</div>
                </div>
              ))}
            </div>
          </>
        ) : null}
      </div>
    </AppShell>
  );
}
