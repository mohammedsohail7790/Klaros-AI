"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, JobProfitability, listProfitability } from "@/lib/api";
import { Skeleton } from "@/components/ui/Skeleton";

function marginColor(pct: number | null): string {
  if (pct === null) return "text-muted";
  if (pct < 15) return "text-danger";
  if (pct < 30) return "text-warning";
  return "text-success";
}

export default function ProfitabilityPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [jobs, setJobs] = useState<JobProfitability[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      setJobs((await listProfitability(token)).jobs);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load profitability data.");
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
        <h1 className="font-display text-2xl text-foreground mb-6">Job Profitability</h1>

        {authLoading || loading ? (
          <Skeleton />
        ) : error ? (
          <div className="rounded-md border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : jobs.length === 0 ? (
          <p className="text-sm text-muted">
            No jobs with actual costs recorded yet — profitability appears once job costs are entered.
          </p>
        ) : (
          <div className="klaros-table-wrap">
            <table className="klaros-table">
              <thead className="bg-surface text-muted">
                <tr>
                  <th className="px-4 py-2">Job</th>
                  <th className="px-4 py-2">Est. revenue</th>
                  <th className="px-4 py-2">Est. margin</th>
                  <th className="px-4 py-2">Actual revenue</th>
                  <th className="px-4 py-2">Actual cost</th>
                  <th className="px-4 py-2">Actual margin</th>
                  <th className="px-4 py-2">Cost breakdown</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((j) => (
                  <tr key={j.job_id} className="border-t border-border">
                    <td className="px-4 py-2">
                      <Link href={`/jobs/${j.job_id}`} className="underline hover:text-foreground">
                        {j.job_number} — {j.title}
                      </Link>
                    </td>
                    <td className="px-4 py-2 text-muted">
                      {j.estimated_revenue ? `$${j.estimated_revenue}` : "—"}
                    </td>
                    <td className={`px-4 py-2 ${marginColor(j.estimated_margin_pct)}`}>
                      {j.estimated_margin_pct !== null ? `${j.estimated_margin_pct}%` : "—"}
                    </td>
                    <td className="px-4 py-2 text-muted">{j.actual_revenue ? `$${j.actual_revenue}` : "—"}</td>
                    <td className="px-4 py-2 text-muted">{j.actual_cost ? `$${j.actual_cost}` : "—"}</td>
                    <td className={`px-4 py-2 font-semibold ${marginColor(j.actual_margin_pct)}`}>
                      {j.actual_margin_pct !== null ? `${j.actual_margin_pct}%` : "—"}
                    </td>
                    <td className="px-4 py-2 text-xs text-muted">
                      {Object.entries(j.cost_breakdown)
                        .map(([cat, amt]) => `${cat}: $${amt}`)
                        .join(", ") || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </AppShell>
  );
}
