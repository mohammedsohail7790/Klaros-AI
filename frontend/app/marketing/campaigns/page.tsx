"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Megaphone } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, Campaign, createCampaign, detectMarketingExceptions, listCampaigns } from "@/lib/api";

import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
const CHANNELS = ["GOOGLE_ADS", "META_ADS", "YOUTUBE_ADS", "LOCAL_SERVICES_ADS", "SEO", "LOCAL", "CONTENT", "OUTBOUND", "REFERRAL", "OTHER"];

export default function CampaignsPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [channel, setChannel] = useState("GOOGLE_ADS");
  const [budget, setBudget] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [detectNotice, setDetectNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      setCampaigns((await listCampaigns(token)).campaigns);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load campaigns.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !name.trim()) return;
    setSubmitting(true);
    try {
      await createCampaign(token, { name: name.trim(), channel, total_budget: budget || undefined });
      setName("");
      setBudget("");
      setShowCreate(false);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to create campaign.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDetectExceptions() {
    if (!token) return;
    setDetecting(true);
    setDetectNotice(null);
    try {
      const result = await detectMarketingExceptions(token);
      setDetectNotice(
        result.flagged_campaign_ids.length > 0
          ? `${result.flagged_campaign_ids.length} campaign(s) flagged — see Exceptions.`
          : "No new campaign exceptions detected."
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to detect exceptions.");
    } finally {
      setDetecting(false);
    }
  }

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <div className="mb-6 flex items-center justify-between">
          <h1 className="font-display text-2xl text-foreground">Campaigns</h1>
          <div className="flex gap-2">
            <button
              disabled={detecting}
              onClick={handleDetectExceptions}
              className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
            >
              {detecting ? "Detecting..." : "Detect exceptions"}
            </button>
            <button onClick={() => setShowCreate((v) => !v)} className="klaros-btn-primary">
              New campaign
            </button>
          </div>
        </div>

        {detectNotice && (
          <div className="mb-4 rounded-md border border-warning/25 bg-warning/[0.07] p-3 text-sm text-warning">
            {detectNotice}
          </div>
        )}

        {showCreate && (
          <form onSubmit={handleCreate} className="mb-6 flex flex-wrap items-end gap-2 rounded-lg border border-border p-4">
            <div>
              <label className="block text-xs text-muted">Name</label>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-muted">Channel</label>
              <select
                value={channel}
                onChange={(e) => setChannel(e.target.value)}
                className="rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
              >
                {CHANNELS.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-muted">Total budget</label>
              <input
                value={budget}
                onChange={(e) => setBudget(e.target.value)}
                placeholder="0.00"
                className="w-28 rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
              />
            </div>
            <button
              type="submit"
              disabled={submitting || !name.trim()}
              className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
            >
              Create
            </button>
          </form>
        )}

        {authLoading || loading ? (
          <Skeleton />
        ) : error ? (
          <div className="rounded-md border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : campaigns.length === 0 ? (
          <EmptyState
            icon={Megaphone}
            title="No campaigns yet."
            action={
              !showCreate && (
                <button onClick={() => setShowCreate(true)} className="klaros-btn-primary">
                  New campaign
                </button>
              )
            }
          />
        ) : (
          <div className="klaros-table-wrap">
            <table className="klaros-table">
              <thead className="bg-surface text-muted">
                <tr>
                  <th className="px-4 py-2">Name</th>
                  <th className="px-4 py-2">Channel</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2">Budget</th>
                </tr>
              </thead>
              <tbody>
                {campaigns.map((c) => (
                  <tr key={c.id} className="border-t border-border">
                    <td className="px-4 py-2">
                      <Link href={`/marketing/campaigns/${c.id}`} className="underline hover:text-foreground">
                        {c.name}
                      </Link>
                    </td>
                    <td className="px-4 py-2 text-muted">{c.channel}</td>
                    <td className="px-4 py-2">
                      <Badge status={c.status}>{c.status}</Badge>
                    </td>
                    <td className="px-4 py-2 text-muted">{c.total_budget ? `$${c.total_budget}` : "—"}</td>
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
