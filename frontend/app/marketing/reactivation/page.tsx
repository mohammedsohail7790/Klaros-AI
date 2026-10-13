"use client";

import { useCallback, useEffect, useState } from "react";
import { Heart, Target } from "lucide-react";
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

import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
export default function ReactivationPage() {
  const toast = useToast();
  const { token, user, loading: authLoading } = useAuth();
  const [campaigns, setCampaigns] = useState<ReactivationCampaignRow[]>([]);
  const [candidates, setCandidates] = useState<ReactivationCandidateRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
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
    try {
      const result = kind === "customers" ? await identifyInactiveCustomers(token, campaignId) : await identifyUnbookedQualifiedLeads(token, campaignId);
      toast.success(`${result.candidate_ids.length} new candidate(s) identified.`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to identify candidates.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell user={user}>
      <div className="mx-auto w-full max-w-6xl px-5 py-8 sm:px-8 sm:py-12">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground mb-6">Database Reactivation</h1>

        <form onSubmit={handleCreate} className="mb-6 flex items-end gap-2">
          <div>
            <label className="block text-xs text-muted">New reactivation campaign</label>
            <input value={name} onChange={(e) => setName(e.target.value)} className="w-64 rounded-xl border border-border-strong bg-surface-muted px-2 py-1.5 text-sm" />
          </div>
          <button type="submit" disabled={busy || !name.trim()} className="klaros-btn-secondary !px-3 !py-1.5 disabled:opacity-50">
            Create
          </button>
        </form>

        {error && <div className="mb-4 rounded-xl border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">{error}</div>}

        {authLoading || loading ? (
          <Skeleton />
        ) : (
          <>
            <h2 className="mb-3 font-display text-[1.35rem] font-semibold tracking-[-0.02em] text-foreground">Campaigns</h2>
            {campaigns.length === 0 ? (
              <div className="mb-6">
                <EmptyState icon={Heart} title="No reactivation campaigns yet." />
              </div>
            ) : (
              <div className="mb-6 space-y-2">
                {campaigns.map((c) => (
                  <div key={c.id} className="flex items-center justify-between rounded-xl border border-border p-3">
                    <span className="text-sm">{c.name}</span>
                    <div className="flex gap-2">
                      <button disabled={busy} onClick={() => handleIdentify(c.id, "customers")} className="text-xs underline text-muted hover:text-foreground">
                        Identify inactive customers
                      </button>
                      <button disabled={busy} onClick={() => handleIdentify(c.id, "leads")} className="text-xs underline text-muted hover:text-foreground">
                        Identify unbooked qualified leads
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            <h2 className="mb-3 font-display text-[1.35rem] font-semibold tracking-[-0.02em] text-foreground">Candidates ({candidates.length})</h2>
            {candidates.length === 0 ? (
              <EmptyState icon={Target} title="No candidates identified yet." />
            ) : (
              <div className="klaros-table-wrap">
                <table className="klaros-table">
                  <thead>
                    <tr>
                      <th className="px-5 py-4">Type</th>
                      <th className="px-5 py-4">Reason</th>
                      <th className="px-5 py-4">Score</th>
                      <th className="px-5 py-4">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {candidates.map((c) => (
                      <tr key={c.id} className="border-t border-border">
                        <td className="px-5 py-4 text-muted">{c.customer_id ? "Customer" : "Lead"}</td>
                        <td className="px-5 py-4">{c.reason}</td>
                        <td className="px-5 py-4 text-muted">{c.score}</td>
                        <td className="px-5 py-4">
                          <Badge status={c.status}>{c.status}</Badge>
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
