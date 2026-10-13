"use client";

import { useState } from "react";
import { Wallet } from "lucide-react";
import AppShell from "@/components/AppShell";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { LineChart } from "@/components/ui/Chart";
import { useAuth } from "@/lib/useAuth";
import { ApiError, CashForecastResult, generateCashForecast } from "@/lib/api";

function confidenceLabel(c: string): string {
  if (c === "HIGH") return "CONFIRMED";
  if (c === "MEDIUM") return "EXPECTED";
  return "LOW CONFIDENCE";
}

export default function CashForecastPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [forecast, setForecast] = useState<CashForecastResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleGenerate() {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      setForecast(await generateCashForecast(token));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to generate cash forecast.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <AppShell user={user}>
      <div className="mx-auto w-full max-w-6xl px-5 py-8 sm:px-8 sm:py-12">
        <PageHeader
          title="13-Week Cash Forecast"
          icon={Wallet}
          actions={
            <button disabled={authLoading || loading} onClick={handleGenerate} className="klaros-btn-secondary">
              {loading ? "Generating..." : "Generate forecast"}
            </button>
          }
        />

        {error && (
          <div className="mb-4 rounded-xl border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">{error}</div>
        )}

        {!forecast ? (
          <EmptyState
            icon={Wallet}
            title="No forecast generated yet — click “Generate forecast” to build one from real open invoices and vendor bills."
          />
        ) : (
          <>
            <div className="mb-6 klaros-card p-4">
              <div className="text-xs text-muted">Starting cash</div>
              <div className="mt-1 text-lg font-semibold">
                {forecast.starting_cash ? `$${forecast.starting_cash}` : "Not connected"}
              </div>
              <div className="mt-1 text-xs text-muted-foreground">Source: {forecast.starting_cash_source}</div>
            </div>

            {forecast.weeks.every((w) => w.projected_balance !== "NOT_CONNECTED") && (
              <div className="mb-6 klaros-card p-5">
                <div className="mb-3 text-sm font-medium text-muted">Projected balance</div>
                <LineChart
                  points={forecast.weeks.map((w) => ({
                    label: new Date(w.week_start).toLocaleDateString(undefined, { month: "short", day: "numeric" }),
                    value: Number(w.projected_balance),
                  }))}
                  formatValue={(v) => `$${v.toLocaleString()}`}
                />
              </div>
            )}

            <div className="klaros-table-wrap">
              <table className="klaros-table">
                <thead>
                  <tr>
                    <th className="px-5 py-4">Week of</th>
                    <th className="px-5 py-4">Inflow</th>
                    <th className="px-5 py-4">Outflow</th>
                    <th className="px-5 py-4">Net</th>
                    <th className="px-5 py-4">Projected balance</th>
                    <th className="px-5 py-4">Items</th>
                  </tr>
                </thead>
                <tbody>
                  {forecast.weeks.map((w) => (
                    <tr key={w.week_start} className="border-t border-border">
                      <td className="px-5 py-4">{w.week_start}</td>
                      <td className="px-5 py-4 text-success">${w.inflow}</td>
                      <td className="px-5 py-4 text-danger">${w.outflow}</td>
                      <td className="px-5 py-4">${w.net}</td>
                      <td className="px-5 py-4 font-semibold">
                        {w.projected_balance === "NOT_CONNECTED" ? "Not connected" : `$${w.projected_balance}`}
                      </td>
                      <td className="px-5 py-4 text-xs text-muted">
                        {w.items.length === 0
                          ? "—"
                          : w.items.map((i) => `${i.source} (${confidenceLabel(i.confidence)})`).join(", ")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </AppShell>
  );
}
