"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  Campaign,
  CampaignPerformance,
  getCampaign,
  recordCampaignSpend,
  setCampaignStatus,
} from "@/lib/api";

export default function CampaignDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { token, user, loading: authLoading } = useAuth();
  const [campaign, setCampaign] = useState<(Campaign & { performance: CampaignPerformance }) | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [spendAmount, setSpendAmount] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!token || !id) return;
    setLoading(true);
    setError(null);
    try {
      setCampaign(await getCampaign(token, id));
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
    setNotice(null);
    try {
      await recordCampaignSpend(token, id, {
        channel: campaign?.channel || "OTHER", amount: spendAmount, spend_date: new Date().toISOString().slice(0, 10),
      });
      setSpendAmount("");
      setNotice("Spend recorded.");
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
        <div className="px-8 py-8 text-sm text-muted">Loading...</div>
      </AppShell>
    );
  }

  if (error && !campaign) {
    return (
      <AppShell user={user}>
        <div className="px-8 py-8">
          <div className="rounded-md border border-red-200 bg-red-50/30 p-4 text-sm text-red-700">{error}</div>
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

        {notice && (
          <div className="mb-4 rounded-md border border-emerald-200 bg-emerald-50/30 p-3 text-sm text-emerald-700">{notice}</div>
        )}
        {error && (
          <div className="mb-4 rounded-md border border-red-200 bg-red-50/30 p-3 text-sm text-red-700">{error}</div>
        )}

        <div className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-4">
          <div className="rounded-lg border border-border p-4">
            <div className="text-xs text-muted">Spend</div>
            <div className="mt-1 text-lg font-semibold">${perf.spend}</div>
          </div>
          <div className="rounded-lg border border-border p-4">
            <div className="text-xs text-muted">Leads / Qualified</div>
            <div className="mt-1 text-lg font-semibold">{perf.leads} / {perf.qualified_leads}</div>
          </div>
          <div className="rounded-lg border border-border p-4">
            <div className="text-xs text-muted">Appointments / Jobs</div>
            <div className="mt-1 text-lg font-semibold">{perf.booked} / {perf.jobs_created}</div>
          </div>
          <div className="rounded-lg border border-border p-4">
            <div className="text-xs text-muted">Jobs closed</div>
            <div className="mt-1 text-lg font-semibold">{perf.jobs_closed}</div>
          </div>
          <div className="rounded-lg border border-border p-4">
            <div className="text-xs text-muted">Revenue</div>
            <div className="mt-1 text-lg font-semibold">${perf.revenue}</div>
          </div>
          <div className="rounded-lg border border-border p-4">
            <div className="text-xs text-muted">Collected revenue</div>
            <div className="mt-1 text-lg font-semibold">${perf.collected_revenue}</div>
          </div>
          <div className="rounded-lg border border-border p-4">
            <div className="text-xs text-muted">CAC</div>
            <div className="mt-1 text-lg font-semibold">{perf.cac ? `$${perf.cac}` : "—"}</div>
            {!perf.cac && <div className="mt-1 text-xs text-amber-700">{perf.cac_note}</div>}
          </div>
          <div className="rounded-lg border border-border p-4">
            <div className="text-xs text-muted">ROAS</div>
            <div className="mt-1 text-lg font-semibold">{perf.roas ? `${perf.roas}x` : "—"}</div>
            {!perf.roas && <div className="mt-1 text-xs text-amber-700">{perf.roas_note}</div>}
          </div>
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
