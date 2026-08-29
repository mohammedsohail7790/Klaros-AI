"use client";

import { useState } from "react";
import AppShell from "@/components/AppShell";
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
      <div className="px-8 py-8">
        <div className="mb-6 flex items-center justify-between">
          <h1 className="text-xl font-semibold">13-Week Cash Forecast</h1>
          <button
            disabled={authLoading || loading}
            onClick={handleGenerate}
            className="rounded-md border border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-900 disabled:opacity-50"
          >
            Generate forecast
          </button>
        </div>

        {error && (
          <div className="mb-4 rounded-md border border-red-900 bg-red-950/30 p-4 text-sm text-red-300">{error}</div>
        )}

        {!forecast ? (
          <p className="text-sm text-neutral-500">
            No forecast generated yet. Click &quot;Generate forecast&quot; to build one from real open invoices and
            vendor bills.
          </p>
        ) : (
          <>
            <div className="mb-6 rounded-lg border border-neutral-800 p-4">
              <div className="text-xs text-neutral-500">Starting cash</div>
              <div className="mt-1 text-lg font-semibold">
                {forecast.starting_cash ? `$${forecast.starting_cash}` : "Not connected"}
              </div>
              <div className="mt-1 text-xs text-neutral-600">Source: {forecast.starting_cash_source}</div>
            </div>

            <div className="overflow-x-auto rounded-lg border border-neutral-800">
              <table className="w-full text-left text-sm">
                <thead className="bg-neutral-950 text-neutral-500">
                  <tr>
                    <th className="px-4 py-2">Week of</th>
                    <th className="px-4 py-2">Inflow</th>
                    <th className="px-4 py-2">Outflow</th>
                    <th className="px-4 py-2">Net</th>
                    <th className="px-4 py-2">Projected balance</th>
                    <th className="px-4 py-2">Items</th>
                  </tr>
                </thead>
                <tbody>
                  {forecast.weeks.map((w) => (
                    <tr key={w.week_start} className="border-t border-neutral-900">
                      <td className="px-4 py-2">{w.week_start}</td>
                      <td className="px-4 py-2 text-emerald-400">${w.inflow}</td>
                      <td className="px-4 py-2 text-red-400">${w.outflow}</td>
                      <td className="px-4 py-2">${w.net}</td>
                      <td className="px-4 py-2 font-semibold">
                        {w.projected_balance === "NOT_CONNECTED" ? "Not connected" : `$${w.projected_balance}`}
                      </td>
                      <td className="px-4 py-2 text-xs text-neutral-500">
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
