"use client";

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { AdsProviderStatus, ApiError, MarketingSummary, getAdsProviderStatus, getMarketingSummary } from "@/lib/api";

function Stat({ label, value, note }: { label: string; value: string | number; note?: string | null }) {
  return (
    <div className="rounded-lg border border-neutral-800 p-4">
      <div className="text-xs text-neutral-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold">{value}</div>
      {note && <div className="mt-1 text-xs text-amber-400">{note}</div>}
    </div>
  );
}

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
        <h1 className="mb-6 text-xl font-semibold">Marketing &amp; Demand Generation</h1>

        {authLoading || loading ? (
          <p className="text-sm text-neutral-500">Loading...</p>
        ) : error ? (
          <div className="rounded-md border border-red-900 bg-red-950/30 p-4 text-sm text-red-300">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : summary ? (
          <>
            {summary.needs_attention && (
              <div className="mb-6 rounded-md border border-amber-800 bg-amber-950/30 p-4 text-sm text-amber-300">
                MARKETING NEEDS ATTENTION — {summary.open_marketing_exception_count} open marketing exception(s).
              </div>
            )}
            <div className="mb-8 grid grid-cols-2 gap-4 md:grid-cols-4">
              <Stat label="Marketing spend" value={`$${summary.marketing_spend}`} />
              <Stat label="Leads" value={summary.leads} />
              <Stat label="Qualified leads" value={summary.qualified_leads} />
              <Stat label="Appointments booked" value={summary.appointments_booked} />
              <Stat label="Jobs won" value={summary.jobs_won} />
              <Stat label="Revenue attributed" value={`$${summary.revenue}`} />
              <Stat label="Collected revenue" value={`$${summary.collected_revenue}`} />
              <Stat
                label="CAC"
                value={summary.cac ? `$${summary.cac}` : "Insufficient data"}
                note={summary.cac ? null : summary.cac_note}
              />
              <Stat
                label="ROAS"
                value={summary.roas ? `${summary.roas}x` : "Insufficient data"}
                note={summary.roas ? null : summary.roas_note}
              />
              <Stat
                label="Conversion rate"
                value={summary.conversion_rate_pct !== null ? `${summary.conversion_rate_pct}%` : "No leads yet"}
              />
              <Stat label="Campaigns" value={summary.campaign_count} />
            </div>

            <h2 className="mb-3 text-sm font-semibold text-neutral-400">Paid ads integration status</h2>
            <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
              {providers.map((p) => (
                <div key={p.provider} className="rounded-lg border border-neutral-800 p-4">
                  <div className="text-sm font-medium">{p.provider.replace(/_/g, " ")}</div>
                  <span className="mt-1 inline-block rounded-full border border-neutral-700 px-2 py-0.5 text-xs text-neutral-400">
                    {p.status}
                  </span>
                  <div className="mt-2 text-xs text-neutral-500">{p.detail}</div>
                </div>
              ))}
            </div>
          </>
        ) : null}
      </div>
    </AppShell>
  );
}
