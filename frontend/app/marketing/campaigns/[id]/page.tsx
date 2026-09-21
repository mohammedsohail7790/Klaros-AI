"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { Skeleton } from "@/components/ui/Skeleton";
import { StatCard } from "@/components/ui/StatCard";
import { useToast } from "@/components/ui/Toast";
import {
  ApiError,
  Campaign,
  CampaignBudgetStatus,
  CampaignPerformance,
  getCampaign,
  getCampaignBudgetStatus,
  recordCampaignSpend,
  setCampaignStatus,
} from "@/lib/api";

export default function CampaignDetailPage() {
  const toast = useToast();
  const { id } = useParams<{ id: string }>();
  const { token, user, loading: authLoading } = useAuth();
  const [campaign, setCampaign] = useState<(Campaign & { performance: CampaignPerformance }) | null>(null);
  const [budgetStatus, setBudgetStatus] = useState<CampaignBudgetStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [spendAmount, setSpendAmount] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!token || !id) return;
    setLoading(true);
    setError(null);
    try {
      const [campaignResult, budgetResult] = await Promise.all([
        getCampaign(token, id),
        getCampaignBudgetStatus(token, id),
      ]);
      setCampaign(campaignResult);
      setBudgetStatus(budgetResult);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load campaign.");
    } finally {
      setLoading(false);
    }
  }, [token, id]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleRecordSpend(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !spendAmount) return;
    setBusy(true);
    try {
      await recordCampaignSpend(token, id, {
        channel: campaign?.channel || "OTHER", amount: spendAmount, spend_date: new Date().toISOString().slice(0, 10),
      });
      setSpendAmount("");
      toast.success("Spend recorded.");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to record spend.");
    } finally {
      setBusy(false);
    }
  }

  async function handleToggleStatus(status: string) {
    if (!token) return;
    setBusy(true);
    try {
      await setCampaignStatus(token, id, status);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to update status.");
    } finally {
      setBusy(false);
    }
  }

  if (authLoading || loading) {
    return (
      <AppShell user={user}>
        <Skeleton />
      </AppShell>
    );
  }

  if (error && !campaign) {
    return (
      <AppShell user={user}>
        <div className="px-8 py-8">
          <div className="rounded-md border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">{error}</div>
        </div>
      </AppShell>
    );
  }

  if (!campaign) return null;
  const perf = campaign.performance;

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <div className="mb-6 flex items-center justify-between">
          <h1 className="font-display text-2xl text-foreground">{campaign.name}</h1>
          <span className="rounded-full border border-border-strong px-3 py-1 text-xs">{campaign.status}</span>
        </div>

        {error && (
          <div className="mb-4 rounded-md border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">{error}</div>
        )}

        {budgetStatus && budgetStatus.budget !== null && (
          <div
            className={`mb-6 rounded-lg border p-4 text-sm ${
              budgetStatus.alert
                ? budgetStatus.alert.includes("overspend")
                  ? "border-danger/25 bg-danger/[0.06] text-danger"
                  : "border-warning/25 bg-warning/[0.07] text-warning"
                : "border-border bg-surface text-muted"
            }`}
          >
            <div className="flex items-center justify-between">
              <span>
                Budget: ${budgetStatus.spend_to_date} of ${budgetStatus.budget} spent
                {budgetStatus.utilization_pct !== null && ` (${budgetStatus.utilization_pct}%)`}
                {budgetStatus.remaining !== null && ` — $${budgetStatus.remaining} remaining`}
              </span>
              {budgetStatus.alert && <span className="font-medium">{budgetStatus.alert}</span>}
            </div>
          </div>
        )}

        <div className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-4">
          <StatCard label="Spend" value={`$${perf.spend}`} tone="accent" />
          <StatCard label="Leads / Qualified" value={`${perf.leads} / ${perf.qualified_leads}`} />
          <StatCard label="Appointments / Jobs" value={`${perf.booked} / ${perf.jobs_created}`} />
          <StatCard label="Jobs closed" value={perf.jobs_closed} tone="success" />
          <StatCard label="Revenue" value={`$${perf.revenue}`} tone="accent" />
          <StatCard label="Collected revenue" value={`$${perf.collected_revenue}`} tone="success" />
          <StatCard label="CAC" value={perf.cac ? `$${perf.cac}` : "—"} note={perf.cac ? null : perf.cac_note} />
          <StatCard label="ROAS" value={perf.roas ? `${perf.roas}x` : "—"} note={perf.roas ? null : perf.roas_note} />
        </div>

        <div className="mb-6 flex flex-wrap items-end gap-4">
          <form onSubmit={handleRecordSpend} className="flex items-end gap-2">
            <div>
              <label className="block text-xs text-muted">Record spend ({campaign.channel})</label>
              <input
                value={spendAmount}
                onChange={(e) => setSpendAmount(e.target.value)}
                placeholder="0.00"
                className="w-28 rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
              />
            </div>
            <button
              type="submit"
              disabled={busy || !spendAmount}
              className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
            >
              Record spend
            </button>
          </form>

          {campaign.status === "DRAFT" && (
            <button disabled={busy} onClick={() => handleToggleStatus("ACTIVE")} className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted">
              Activate
            </button>
          )}
          {campaign.status === "ACTIVE" && (
            <button disabled={busy} onClick={() => handleToggleStatus("PAUSED")} className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted">
              Pause
            </button>
          )}
        </div>
      </div>
    </AppShell>
  );
}
