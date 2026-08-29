"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, Campaign, createCampaign, listCampaigns } from "@/lib/api";

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

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <div className="mb-6 flex items-center justify-between">
          <h1 className="text-xl font-semibold">Campaigns</h1>
          <button
            onClick={() => setShowCreate((v) => !v)}
            className="rounded-md border border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-900"
          >
            New campaign
          </button>
        </div>

        {showCreate && (
          <form onSubmit={handleCreate} className="mb-6 flex flex-wrap items-end gap-2 rounded-lg border border-neutral-800 p-4">
            <div>
              <label className="block text-xs text-neutral-500">Name</label>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="rounded-md border border-neutral-700 bg-neutral-900 px-2 py-1.5 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-neutral-500">Channel</label>
              <select
                value={channel}
                onChange={(e) => setChannel(e.target.value)}
                className="rounded-md border border-neutral-700 bg-neutral-900 px-2 py-1.5 text-sm"
              >
                {CHANNELS.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-neutral-500">Total budget</label>
              <input
                value={budget}
                onChange={(e) => setBudget(e.target.value)}
                placeholder="0.00"
                className="w-28 rounded-md border border-neutral-700 bg-neutral-900 px-2 py-1.5 text-sm"
              />
            </div>
            <button
              type="submit"
              disabled={submitting || !name.trim()}
              className="rounded-md border border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-900 disabled:opacity-50"
            >
              Create
            </button>
          </form>
        )}

        {authLoading || loading ? (
          <p className="text-sm text-neutral-500">Loading...</p>
        ) : error ? (
          <div className="rounded-md border border-red-900 bg-red-950/30 p-4 text-sm text-red-300">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : campaigns.length === 0 ? (
          <p className="text-sm text-neutral-500">No campaigns yet.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-neutral-800">
            <table className="w-full text-left text-sm">
              <thead className="bg-neutral-950 text-neutral-500">
                <tr>
                  <th className="px-4 py-2">Name</th>
                  <th className="px-4 py-2">Channel</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2">Budget</th>
                </tr>
              </thead>
              <tbody>
                {campaigns.map((c) => (
                  <tr key={c.id} className="border-t border-neutral-900">
                    <td className="px-4 py-2">
                      <Link href={`/marketing/campaigns/${c.id}`} className="underline hover:text-white">
                        {c.name}
                      </Link>
                    </td>
                    <td className="px-4 py-2 text-neutral-400">{c.channel}</td>
                    <td className="px-4 py-2">
                      <span className="rounded-full border border-neutral-700 px-2 py-0.5 text-xs">{c.status}</span>
                    </td>
                    <td className="px-4 py-2 text-neutral-400">{c.total_budget ? `$${c.total_budget}` : "—"}</td>
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
