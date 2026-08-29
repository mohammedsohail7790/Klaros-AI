"use client";

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  ReactivationCampaignRow,
  ReactivationCandidateRow,
  createReactivationCampaign,
  identifyInactiveCustomers,
  identifyUnbookedQualifiedLeads,
  listReactivationCampaigns,
  listReactivationCandidates,
} from "@/lib/api";

export default function ReactivationPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [campaigns, setCampaigns] = useState<ReactivationCampaignRow[]>([]);
  const [candidates, setCandidates] = useState<ReactivationCandidateRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [campaignsResult, candidatesResult] = await Promise.all([
        listReactivationCampaigns(token), listReactivationCandidates(token),
      ]);
      setCampaigns(campaignsResult.campaigns);
      setCandidates(candidatesResult.candidates);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load reactivation data.");
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
    setBusy(true);
    try {
      await createReactivationCampaign(token, name.trim());
      setName("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to create campaign.");
    } finally {
      setBusy(false);
    }
  }

  async function handleIdentify(campaignId: string, kind: "customers" | "leads") {
    if (!token) return;
    setBusy(true);
    setNotice(null);
    try {
      const result = kind === "customers" ? await identifyInactiveCustomers(token, campaignId) : await identifyUnbookedQualifiedLeads(token, campaignId);
      setNotice(`${result.candidate_ids.length} new candidate(s) identified.`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to identify candidates.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <h1 className="mb-6 text-xl font-semibold">Database Reactivation</h1>

        <form onSubmit={handleCreate} className="mb-6 flex items-end gap-2">
          <div>
            <label className="block text-xs text-neutral-500">New reactivation campaign</label>
            <input value={name} onChange={(e) => setName(e.target.value)} className="w-64 rounded-md border border-neutral-700 bg-neutral-900 px-2 py-1.5 text-sm" />
          </div>
          <button type="submit" disabled={busy || !name.trim()} className="rounded-md border border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-900 disabled:opacity-50">
            Create
          </button>
        </form>

        {notice && <div className="mb-4 rounded-md border border-emerald-900 bg-emerald-950/30 p-3 text-sm text-emerald-300">{notice}</div>}
        {error && <div className="mb-4 rounded-md border border-red-900 bg-red-950/30 p-3 text-sm text-red-300">{error}</div>}

        {authLoading || loading ? (
          <p className="text-sm text-neutral-500">Loading...</p>
        ) : (
          <>
            <h2 className="mb-3 text-sm font-medium text-neutral-300">Campaigns</h2>
            {campaigns.length === 0 ? (
              <p className="mb-6 text-sm text-neutral-500">No reactivation campaigns yet.</p>
            ) : (
              <div className="mb-6 space-y-2">
                {campaigns.map((c) => (
                  <div key={c.id} className="flex items-center justify-between rounded-lg border border-neutral-800 p-3">
                    <span className="text-sm">{c.name}</span>
                    <div className="flex gap-2">
                      <button disabled={busy} onClick={() => handleIdentify(c.id, "customers")} className="text-xs underline text-neutral-400 hover:text-white">
                        Identify inactive customers
                      </button>
                      <button disabled={busy} onClick={() => handleIdentify(c.id, "leads")} className="text-xs underline text-neutral-400 hover:text-white">
                        Identify unbooked qualified leads
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            <h2 className="mb-3 text-sm font-medium text-neutral-300">Candidates ({candidates.length})</h2>
            {candidates.length === 0 ? (
              <p className="text-sm text-neutral-500">No candidates identified yet.</p>
            ) : (
              <div className="overflow-x-auto rounded-lg border border-neutral-800">
                <table className="w-full text-left text-sm">
                  <thead className="bg-neutral-950 text-neutral-500">
                    <tr>
                      <th className="px-4 py-2">Type</th>
                      <th className="px-4 py-2">Reason</th>
                      <th className="px-4 py-2">Score</th>
                      <th className="px-4 py-2">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {candidates.map((c) => (
                      <tr key={c.id} className="border-t border-neutral-900">
                        <td className="px-4 py-2 text-neutral-400">{c.customer_id ? "Customer" : "Lead"}</td>
                        <td className="px-4 py-2">{c.reason}</td>
                        <td className="px-4 py-2 text-neutral-500">{c.score}</td>
                        <td className="px-4 py-2">
                          <span className="rounded-full border border-neutral-700 px-2 py-0.5 text-xs">{c.status}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
    </AppShell>
  );
}
