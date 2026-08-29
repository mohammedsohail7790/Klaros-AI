"use client";

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import Link from "next/link";
import {
  ApiError,
  AutonomyStats,
  CrmMetrics,
  FinanceSummary,
  MarketingSummary,
  MorningBriefData,
  RetentionSummary,
  getAutonomyStats,
  getCrmMetrics,
  getFinanceSummary,
  getLatestMorningBrief,
  getMarketingSummary,
  getRetentionSummary,
} from "@/lib/api";

const PENDING_MODULES: string[] = [];

const METRIC_LABELS: { key: keyof CrmMetrics; label: string }[] = [
  { key: "new_leads_today", label: "New leads today" },
  { key: "qualified_leads", label: "Qualified leads" },
  { key: "appointments_today", label: "Appointments today" },
  { key: "conversion_rate_pct", label: "Conversion rate" },
  { key: "uncontacted_leads", label: "Uncontacted leads" },
  { key: "at_risk_leads", label: "At-risk leads" },
];

export default function DashboardPage() {
  const { token, user, loading: authLoading, error: authError } = useAuth();
  const [metrics, setMetrics] = useState<CrmMetrics | null>(null);
  const [finance, setFinance] = useState<FinanceSummary | null>(null);
  const [marketing, setMarketing] = useState<MarketingSummary | null>(null);
  const [retention, setRetention] = useState<RetentionSummary | null>(null);
  const [brief, setBrief] = useState<MorningBriefData | null>(null);
  const [autonomy, setAutonomy] = useState<AutonomyStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [metricsResult, financeResult, marketingResult, retentionResult, briefResult, autonomyResult] =
        await Promise.all([
          getCrmMetrics(token),
          getFinanceSummary(token),
          getMarketingSummary(token),
          getRetentionSummary(token),
          getLatestMorningBrief(token),
          getAutonomyStats(token),
        ]);
      setMetrics(metricsResult);
      setFinance(financeResult);
      setMarketing(marketingResult);
      setRetention(retentionResult);
      setBrief(briefResult);
      setAutonomy(autonomyResult);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load business metrics.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  if (authError) return <p className="p-8 text-sm text-red-400">{authError}</p>;

  return (
    <AppShell user={user}>
      <div className="px-8 py-10">
        <header className="mb-8 flex items-center justify-between border-b border-neutral-800 pb-4">
          <div>
            <h1 className="text-xl font-semibold">Owner Cockpit</h1>
            {user && (
              <p className="text-sm text-neutral-500">
                Signed in as {user.full_name} · {user.email} · role {user.role}
              </p>
            )}
          </div>
        </header>

        <section className="mb-8">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-medium text-neutral-300">Autonomy status — today</h2>
            <Link href="/settings/automation" className="text-xs text-neutral-500 underline">
              Configure automation
            </Link>
          </div>
          {autonomy ? (
            autonomy.total === 0 ? (
              <p className="rounded-lg border border-neutral-800 bg-neutral-950 p-4 text-sm text-neutral-500">
                No actions yet today.
              </p>
            ) : (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <div className="rounded-lg border border-emerald-900 bg-emerald-950/20 p-4">
                  <div className="text-2xl font-semibold text-emerald-300">{autonomy.automatic}</div>
                  <div className="text-xs text-neutral-500">Automatic</div>
                </div>
                <div className="rounded-lg border border-amber-900 bg-amber-950/20 p-4">
                  <div className="text-2xl font-semibold text-amber-300">{autonomy.approval_required}</div>
                  <div className="text-xs text-neutral-500">Awaiting approval</div>
                </div>
                <div className="rounded-lg border border-red-900 bg-red-950/20 p-4">
                  <div className="text-2xl font-semibold text-red-300">{autonomy.blocked}</div>
                  <div className="text-xs text-neutral-500">Blocked</div>
                </div>
                <div className="rounded-lg border border-neutral-700 bg-neutral-900/40 p-4">
                  <div className="text-2xl font-semibold text-neutral-300">{autonomy.failed}</div>
                  <div className="text-xs text-neutral-500">Failed</div>
                </div>
              </div>
            )
          ) : (
            <p className="text-sm text-neutral-500">Loading...</p>
          )}
        </section>

        <section className="mb-8">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-medium text-neutral-300">Morning Brief</h2>
            <Link href="/morning-brief" className="text-xs text-neutral-500 underline">
              View full brief
            </Link>
          </div>
          {brief && brief.brief_id ? (
            <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-6">
              <div className="mb-2 flex items-center gap-2">
                <span className="rounded-full border border-neutral-700 px-2 py-0.5 text-xs">
                  {brief.mode === "DETERMINISTIC" ? "DETERMINISTIC SUMMARY — AI NOT CONNECTED" : "AI"}
                </span>
              </div>
              <p className="mb-4">{brief.headline}</p>
              {brief.insights.filter((i) => i.priority === "HIGH").length > 0 && (
                <div className="mb-3">
                  <h3 className="mb-1 text-xs font-medium text-red-300">Needs attention</h3>
                  <ul className="space-y-1 text-sm text-neutral-400">
                    {brief.insights
                      .filter((i) => i.priority === "HIGH")
                      .slice(0, 3)
                      .map((i) => (
                        <li key={i.insight_id}>{i.summary}</li>
                      ))}
                  </ul>
                </div>
              )}
              {brief.recommendations.filter((r) => r.status === "PENDING").length > 0 && (
                <div>
                  <h3 className="mb-1 text-xs font-medium text-neutral-300">Recommended actions</h3>
                  <ul className="space-y-1 text-sm text-neutral-400">
                    {brief.recommendations
                      .filter((r) => r.status === "PENDING")
                      .slice(0, 3)
                      .map((r) => (
                        <li key={r.recommendation_id}>{r.what}</li>
                      ))}
                  </ul>
                </div>
              )}
              {brief.recommendations.filter((r) => r.status === "APPROVAL_REQUESTED").length > 0 && (
                <div className="mt-3">
                  <h3 className="mb-1 text-xs font-medium text-amber-300">Awaiting your approval</h3>
                  <ul className="space-y-1 text-sm text-neutral-400">
                    {brief.recommendations
                      .filter((r) => r.status === "APPROVAL_REQUESTED")
                      .slice(0, 3)
                      .map((r) => (
                        <li key={r.recommendation_id}>
                          {r.what} —{" "}
                          <Link href="/approvals" className="underline text-amber-400">
                            review
                          </Link>
                        </li>
                      ))}
                  </ul>
                </div>
              )}
            </div>
          ) : (
            <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-6 text-sm text-neutral-500">
              No brief generated yet. <Link href="/morning-brief" className="underline">Generate one</Link>.
            </div>
          )}
        </section>

        <section className="mb-8">
          <h2 className="mb-3 text-sm font-medium text-neutral-300">Today — CRM</h2>
          {authLoading || loading ? (
            <p className="text-sm text-neutral-500">Loading metrics...</p>
          ) : error ? (
            <div className="rounded-md border border-red-900 bg-red-950/30 p-4 text-sm text-red-300">
              {error}{" "}
              <button onClick={load} className="ml-2 underline">
                Retry
              </button>
            </div>
          ) : metrics ? (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
              {METRIC_LABELS.map(({ key, label }) => (
                <div key={key} className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
                  <p className="text-2xl font-semibold">
                    {key === "conversion_rate_pct" ? `${metrics[key]}%` : metrics[key]}
                  </p>
                  <p className="mt-1 text-xs text-neutral-500">{label}</p>
                </div>
              ))}
            </div>
          ) : null}
        </section>

        <section className="mb-8">
          <h2 className="mb-3 text-sm font-medium text-neutral-300">Business Health — Finance</h2>
          {finance?.needs_attention && (
            <div className="mb-4 rounded-md border border-amber-800 bg-amber-950/30 p-4 text-sm text-amber-300">
              FINANCE NEEDS ATTENTION — {finance.overdue_invoice_count} overdue invoice(s),{" "}
              {finance.open_finance_exception_count} open finance exception(s).
            </div>
          )}
          {finance && (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
              <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
                <p className="text-2xl font-semibold">${finance.total_ar}</p>
                <p className="mt-1 text-xs text-neutral-500">Total AR outstanding</p>
              </div>
              <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
                <p className="text-2xl font-semibold">{finance.overdue_invoice_count}</p>
                <p className="mt-1 text-xs text-neutral-500">Overdue invoices</p>
              </div>
              <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
                <p className="text-2xl font-semibold">{finance.pending_approval_invoice_count}</p>
                <p className="mt-1 text-xs text-neutral-500">Pending approval</p>
              </div>
              <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
                <p className="text-2xl font-semibold">${finance.total_paid}</p>
                <p className="mt-1 text-xs text-neutral-500">Total paid</p>
              </div>
            </div>
          )}
        </section>

        <section className="mb-8">
          <h2 className="mb-3 text-sm font-medium text-neutral-300">Marketing &amp; Demand Generation</h2>
          {marketing?.needs_attention && (
            <div className="mb-4 rounded-md border border-amber-800 bg-amber-950/30 p-4 text-sm text-amber-300">
              MARKETING NEEDS ATTENTION — {marketing.open_marketing_exception_count} open marketing exception(s).
            </div>
          )}
          {marketing && (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
              <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
                <p className="text-2xl font-semibold">${marketing.marketing_spend}</p>
                <p className="mt-1 text-xs text-neutral-500">Marketing spend</p>
              </div>
              <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
                <p className="text-2xl font-semibold">{marketing.leads}</p>
                <p className="mt-1 text-xs text-neutral-500">Leads</p>
              </div>
              <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
                <p className="text-2xl font-semibold">{marketing.jobs_won}</p>
                <p className="mt-1 text-xs text-neutral-500">Jobs won</p>
              </div>
              <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
                <p className="text-2xl font-semibold">${marketing.revenue}</p>
                <p className="mt-1 text-xs text-neutral-500">Revenue attributed</p>
              </div>
              <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
                <p className="text-2xl font-semibold">{marketing.cac ? `$${marketing.cac}` : "—"}</p>
                <p className="mt-1 text-xs text-neutral-500">CAC</p>
              </div>
              <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
                <p className="text-2xl font-semibold">{marketing.roas ? `${marketing.roas}x` : "—"}</p>
                <p className="mt-1 text-xs text-neutral-500">ROAS</p>
              </div>
            </div>
          )}
        </section>

        <section className="mb-8">
          <h2 className="mb-3 text-sm font-medium text-neutral-300">Retention &amp; Referral</h2>
          {retention?.needs_attention && (
            <div className="mb-4 rounded-md border border-amber-800 bg-amber-950/30 p-4 text-sm text-amber-300">
              RETENTION NEEDS ATTENTION — {retention.open_retention_exception_count} open retention exception(s).
            </div>
          )}
          {retention && (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
              <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
                <p className="text-2xl font-semibold">{retention.at_risk_customers}</p>
                <p className="mt-1 text-xs text-neutral-500">At-risk customers</p>
              </div>
              <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
                <p className="text-2xl font-semibold">{retention.retention_opportunities_open}</p>
                <p className="mt-1 text-xs text-neutral-500">Retention opportunities</p>
              </div>
              <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
                <p className="text-2xl font-semibold">${retention.repeat_customer_revenue}</p>
                <p className="mt-1 text-xs text-neutral-500">Repeat revenue</p>
              </div>
              <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
                <p className="text-2xl font-semibold">{retention.referral_leads}</p>
                <p className="mt-1 text-xs text-neutral-500">Referral leads</p>
              </div>
              <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
                <p className="text-2xl font-semibold">${retention.referral_revenue}</p>
                <p className="mt-1 text-xs text-neutral-500">Referral revenue</p>
              </div>
              <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
                <p className="text-2xl font-semibold">{retention.negative_feedback_count}</p>
                <p className="mt-1 text-xs text-neutral-500">Review issues</p>
              </div>
            </div>
          )}
        </section>

        {PENDING_MODULES.length > 0 && (
          <section className="rounded-lg border border-neutral-800 bg-neutral-950 p-6">
            <h2 className="mb-2 text-sm font-medium text-neutral-300">Foundation status</h2>
            <p className="text-sm text-neutral-500">
              Authentication, multi-tenancy, RBAC, the event bus, and CRM (leads/customers/
              appointments) are live. The modules below are not yet built — this cockpit will never
              show placeholder numbers for them.
            </p>
            <ul className="mt-4 space-y-2">
              {PENDING_MODULES.map((m) => (
                <li key={m} className="flex items-center justify-between text-sm">
                  <span className="text-neutral-300">{m}</span>
                  <span className="rounded-full border border-neutral-700 px-2 py-0.5 text-xs text-neutral-500">
                    not connected
                  </span>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </AppShell>
  );
}
