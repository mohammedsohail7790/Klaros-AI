"use client";

import { useCallback, useEffect, useState } from "react";
import { Gift, Users, Award } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { StatCard } from "@/components/ui/StatCard";
import {
  ApiError,
  ReferralProgramRow,
  ReferralRewardRow,
  ReferralRow,
  approveReferralReward,
  convertReferralToLead,
  createReferral,
  createReferralProgram,
  getOrCreateReferralCode,
  issueReferralReward,
  listReferralPrograms,
  listReferralRewards,
  listReferrals,
  rejectReferralReward,
} from "@/lib/api";

import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
export default function ReferralsPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [programs, setPrograms] = useState<ReferralProgramRow[]>([]);
  const [referrals, setReferrals] = useState<ReferralRow[]>([]);
  const [rewards, setRewards] = useState<ReferralRewardRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [newProgramName, setNewProgramName] = useState("");
  const [newRewardAmount, setNewRewardAmount] = useState("");
  const [codeProgramId, setCodeProgramId] = useState("");
  const [codeCustomerId, setCodeCustomerId] = useState("");
  const [generatedCode, setGeneratedCode] = useState<{ code: string; code_id: string } | null>(null);
  const [newReferralId, setNewReferralId] = useState<string | null>(null);
  const [leadName, setLeadName] = useState("");
  const [leadPhone, setLeadPhone] = useState("");
  const [leadEmail, setLeadEmail] = useState("");
  const [leadService, setLeadService] = useState("");

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [programsResult, referralsResult, rewardsResult] = await Promise.all([
        listReferralPrograms(token),
        listReferrals(token),
        listReferralRewards(token),
      ]);
      setPrograms(programsResult.programs);
      setReferrals(referralsResult.referrals);
      setRewards(rewardsResult.rewards);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load referrals.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleCreateProgram() {
    if (!token || !newProgramName.trim()) return;
    setBusy(true);
    try {
      await createReferralProgram(token, {
        name: newProgramName.trim(),
        reward_type: "credit",
        reward_amount: newRewardAmount || undefined,
      });
      setNewProgramName("");
      setNewRewardAmount("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to create program.");
    } finally {
      setBusy(false);
    }
  }

  async function handleApprove(id: string, approved: boolean) {
    if (!token) return;
    setBusy(true);
    try {
      if (approved) {
        await approveReferralReward(token, id);
      } else {
        await rejectReferralReward(token, id);
      }
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to decide reward.");
    } finally {
      setBusy(false);
    }
  }

  async function handleGenerateCode() {
    if (!token || !codeProgramId.trim() || !codeCustomerId.trim()) return;
    setBusy(true);
    try {
      const result = await getOrCreateReferralCode(token, codeProgramId.trim(), codeCustomerId.trim());
      setGeneratedCode(result);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to generate referral code.");
    } finally {
      setBusy(false);
    }
  }

  async function handleCreateReferral() {
    if (!token || !generatedCode) return;
    setBusy(true);
    try {
      const result = await createReferral(token, generatedCode.code_id);
      setNewReferralId(result.referral_id);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to create referral.");
    } finally {
      setBusy(false);
    }
  }

  async function handleConvertToLead() {
    if (!token || !newReferralId || !leadName.trim()) return;
    setBusy(true);
    try {
      await convertReferralToLead(token, newReferralId, {
        name: leadName.trim(),
        phone: leadPhone.trim() || undefined,
        email: leadEmail.trim() || undefined,
        service_requested: leadService.trim() || undefined,
      });
      setLeadName("");
      setLeadPhone("");
      setLeadEmail("");
      setLeadService("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to convert referral to lead.");
    } finally {
      setBusy(false);
    }
  }

  async function handleIssue(id: string) {
    if (!token) return;
    setBusy(true);
    try {
      await issueReferralReward(token, id);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to issue reward.");
    } finally {
      setBusy(false);
    }
  }

  const leads = referrals.filter((r) => r.status !== "CREATED" && r.status !== "CLICKED").length;
  const qualified = referrals.filter((r) => ["QUALIFIED", "BOOKED", "CONVERTED", "REWARDED"].includes(r.status)).length;
  const booked = referrals.filter((r) => ["BOOKED", "CONVERTED", "REWARDED"].includes(r.status)).length;
  const converted = referrals.filter((r) => ["CONVERTED", "REWARDED"].includes(r.status)).length;
  const revenue = referrals.reduce((sum, r) => sum + (r.collected_amount ? Number(r.collected_amount) : 0), 0);

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <h1 className="font-display text-2xl text-foreground mb-6">Referrals</h1>

        {authLoading || loading ? (
          <Skeleton />
        ) : error ? (
          <div className="rounded-md border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">Retry</button>
          </div>
        ) : (
          <>
            <div className="mb-8 grid grid-cols-2 gap-4 md:grid-cols-5">
              <StatCard label="Referral leads" value={leads} icon={Users} />
              <StatCard label="Qualified" value={qualified} />
              <StatCard label="Booked" value={booked} />
              <StatCard label="Converted" value={converted} icon={Award} tone="success" />
              <StatCard label="Collected revenue" value={`$${revenue.toFixed(2)}`} icon={Gift} tone="accent" />
            </div>

            <h2 className="mb-3 text-sm font-medium text-muted">Programs</h2>
            <div className="mb-4 flex flex-wrap items-end gap-2">
              <div>
                <label className="block text-xs text-muted">Program name</label>
                <input
                  value={newProgramName}
                  onChange={(e) => setNewProgramName(e.target.value)}
                  className="rounded-md border border-border-strong bg-surface-muted px-2 py-1 text-sm"
                />
              </div>
              <div>
                <label className="block text-xs text-muted">Reward amount ($, optional)</label>
                <input
                  value={newRewardAmount}
                  onChange={(e) => setNewRewardAmount(e.target.value)}
                  className="rounded-md border border-border-strong bg-surface-muted px-2 py-1 text-sm"
                />
              </div>
              <button disabled={busy || !newProgramName.trim()} onClick={handleCreateProgram} className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50">
                Create program
              </button>
            </div>
            {programs.length === 0 ? (
              <div className="mb-6">
                <EmptyState icon={Gift} title="No referral programs yet." />
              </div>
            ) : (
              <div className="mb-8 klaros-table-wrap">
                <table className="klaros-table">
                  <thead className="bg-surface text-muted">
                    <tr>
                      <th className="px-4 py-2">Name</th>
                      <th className="px-4 py-2">Reward</th>
                      <th className="px-4 py-2">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {programs.map((p) => (
                      <tr key={p.id} className="border-t border-border">
                        <td className="px-4 py-2">{p.name}</td>
                        <td className="px-4 py-2 text-muted">{p.reward_amount ? `$${p.reward_amount} ${p.reward_type}` : p.reward_type}</td>
                        <td className="px-4 py-2">
                          <Badge status={p.status}>{p.status}</Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <h2 className="mb-3 text-sm font-medium text-muted">Referral codes</h2>
            <div className="mb-4 flex flex-wrap items-end gap-2">
              <div>
                <label className="block text-xs text-muted">Program ID</label>
                <input
                  value={codeProgramId}
                  onChange={(e) => setCodeProgramId(e.target.value)}
                  className="w-64 rounded-md border border-border-strong bg-surface-muted px-2 py-1 text-sm"
                />
              </div>
              <div>
                <label className="block text-xs text-muted">Referrer customer ID</label>
                <input
                  value={codeCustomerId}
                  onChange={(e) => setCodeCustomerId(e.target.value)}
                  className="w-64 rounded-md border border-border-strong bg-surface-muted px-2 py-1 text-sm"
                />
              </div>
              <button
                disabled={busy || !codeProgramId.trim() || !codeCustomerId.trim()}
                onClick={handleGenerateCode}
                className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
              >
                Get or create code
              </button>
            </div>
            {generatedCode && (
              <div className="mb-6 rounded-md border border-border p-4 text-sm">
                <p className="text-muted">
                  Code: <span className="font-mono">{generatedCode.code}</span>
                </p>
                <button
                  disabled={busy}
                  onClick={handleCreateReferral}
                  className="mt-2 rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
                >
                  Create referral from this code
                </button>
              </div>
            )}

            {newReferralId && (
              <div className="mb-6 rounded-md border border-border p-4">
                <p className="mb-2 text-sm text-muted">
                  Referral created ({newReferralId}). Convert to a lead:
                </p>
                <div className="flex flex-wrap items-end gap-2">
                  <div>
                    <label className="block text-xs text-muted">Name</label>
                    <input value={leadName} onChange={(e) => setLeadName(e.target.value)} className="rounded-md border border-border-strong bg-surface-muted px-2 py-1 text-sm" />
                  </div>
                  <div>
                    <label className="block text-xs text-muted">Phone</label>
                    <input value={leadPhone} onChange={(e) => setLeadPhone(e.target.value)} className="rounded-md border border-border-strong bg-surface-muted px-2 py-1 text-sm" />
                  </div>
                  <div>
                    <label className="block text-xs text-muted">Email</label>
                    <input value={leadEmail} onChange={(e) => setLeadEmail(e.target.value)} className="rounded-md border border-border-strong bg-surface-muted px-2 py-1 text-sm" />
                  </div>
                  <div>
                    <label className="block text-xs text-muted">Service requested</label>
                    <input value={leadService} onChange={(e) => setLeadService(e.target.value)} className="rounded-md border border-border-strong bg-surface-muted px-2 py-1 text-sm" />
                  </div>
                  <button
                    disabled={busy || !leadName.trim()}
                    onClick={handleConvertToLead}
                    className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
                  >
                    Convert to lead
                  </button>
                </div>
              </div>
            )}

            <h2 className="mb-3 text-sm font-medium text-muted">Referrals</h2>
            {referrals.length === 0 ? (
              <div className="mb-8">
                <EmptyState icon={Users} title="No referrals yet." />
              </div>
            ) : (
              <div className="mb-8 klaros-table-wrap">
                <table className="klaros-table">
                  <thead className="bg-surface text-muted">
                    <tr>
                      <th className="px-4 py-2">Status</th>
                      <th className="px-4 py-2">Revenue</th>
                      <th className="px-4 py-2">Collected</th>
                    </tr>
                  </thead>
                  <tbody>
                    {referrals.map((r) => (
                      <tr key={r.id} className="border-t border-border">
                        <td className="px-4 py-2">
                          <Badge status={r.status}>{r.status}</Badge>
                        </td>
                        <td className="px-4 py-2 text-muted">{r.revenue_amount ? `$${r.revenue_amount}` : "—"}</td>
                        <td className="px-4 py-2 text-muted">{r.collected_amount ? `$${r.collected_amount}` : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <h2 className="mb-3 text-sm font-medium text-muted">Rewards</h2>
            {rewards.length === 0 ? (
              <EmptyState icon={Award} title="No referral rewards yet." />
            ) : (
              <div className="klaros-table-wrap">
                <table className="klaros-table">
                  <thead className="bg-surface text-muted">
                    <tr>
                      <th className="px-4 py-2">Amount</th>
                      <th className="px-4 py-2">Status</th>
                      <th className="px-4 py-2"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {rewards.map((rw) => (
                      <tr key={rw.id} className="border-t border-border">
                        <td className="px-4 py-2">${rw.amount}</td>
                        <td className="px-4 py-2">
                          <Badge status={rw.status}>{rw.status}</Badge>
                        </td>
                        <td className="px-4 py-2 space-x-2">
                          {rw.status === "PENDING" && (
                            <>
                              <button disabled={busy} onClick={() => handleApprove(rw.id, true)} className="text-xs underline text-success hover:text-foreground">Approve</button>
                              <button disabled={busy} onClick={() => handleApprove(rw.id, false)} className="text-xs underline text-danger hover:text-foreground">Reject</button>
                            </>
                          )}
                          {rw.status === "APPROVED" && (
                            <button disabled={busy} onClick={() => handleIssue(rw.id)} className="text-xs underline text-muted hover:text-foreground">Issue</button>
                          )}
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
