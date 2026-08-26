"use client";

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, CrmMetrics, getCrmMetrics } from "@/lib/api";

const PENDING_MODULES = [
  "AI Morning Brief",
  "Needs Your Attention (exception engine)",
  "Approvals",
  "AI Handled",
  "Business Health (Finance)",
];

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
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      setMetrics(await getCrmMetrics(token));
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
      </div>
    </AppShell>
  );
}
