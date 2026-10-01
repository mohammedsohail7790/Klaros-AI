"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { HandCoins } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  MedicalTourismProvider,
  MedicalTourismReferralCommission,
  ReferralRow,
  createReferralCommission,
  listMedicalTourismProviders,
  listReferralCommissions,
  listReferrals,
  updateReferralCommissionStatus,
} from "@/lib/api";

import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";

const STATUS_TABS = ["ALL", "PENDING", "CONFIRMED", "CANCELLED"];
const NEXT_STATUS: Record<string, string[]> = {
  PENDING: ["CONFIRMED", "CANCELLED"],
  CONFIRMED: ["CANCELLED"],
  CANCELLED: [],
};

export default function MedicalTourismReferralCommissionsPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [commissions, setCommissions] = useState<MedicalTourismReferralCommission[]>([]);
  const [referrals, setReferrals] = useState<ReferralRow[]>([]);
  const [providers, setProviders] = useState<MedicalTourismProvider[]>([]);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState("ALL");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  const providerName = useMemo(() => {
    const map = new Map(providers.map((p) => [p.id, p.name]));
    return (id: string | null) => (id ? map.get(id) || id : "—");
  }, [providers]);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [result, referralResult, providerResult] = await Promise.all([
        listReferralCommissions(token, { status: status === "ALL" ? undefined : status, limit: 50 }),
        listReferrals(token),
        listMedicalTourismProviders(token, { limit: 100 }),
      ]);
      setCommissions(result.referral_commissions);
      setTotal(result.total);
      setReferrals(referralResult.referrals);
      setProviders(providerResult.providers);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load referral commissions.");
    } finally {
      setLoading(false);
    }
  }, [token, status]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleStatusChange(commissionId: string, newStatus: string) {
    if (!token) return;
    setBusyId(commissionId);
    try {
      await updateReferralCommissionStatus(token, commissionId, newStatus);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not update commission.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <PageHeader
          title={`Referral Commissions (${total})`}
          description="Cross-border provider referral commissions — a Medical Tourism extension of the tenant's Referrals. Not to be confused with Retention's customer loyalty referral program."
          icon={HandCoins}
          actions={
            <button onClick={() => setShowCreate(true)} className="klaros-btn-primary">
              New commission
            </button>
          }
        />

        <div className="mb-4 flex flex-wrap items-center gap-2">
          {STATUS_TABS.map((s) => (
            <button
              key={s}
              onClick={() => setStatus(s)}
              className={`rounded-full border px-3 py-1 text-xs ${
                status === s ? "border-foreground bg-surface text-foreground" : "border-border-strong text-muted"
              }`}
            >
              {s}
            </button>
          ))}
        </div>

        {authLoading || loading ? (
          <Skeleton />
        ) : error ? (
          <div className="rounded-md border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : commissions.length === 0 ? (
          <EmptyState
            icon={HandCoins}
            title="No referral commissions yet."
            action={
              <button onClick={() => setShowCreate(true)} className="klaros-btn-primary">
                New commission
              </button>
            }
          />
        ) : (
          <div className="klaros-table-wrap">
            <table className="klaros-table">
              <thead className="bg-surface text-muted">
                <tr>
                  <th className="px-4 py-2">Referral</th>
                  <th className="px-4 py-2">Provider</th>
                  <th className="px-4 py-2">Basis</th>
                  <th className="px-4 py-2">Amount</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2">Actions</th>
                </tr>
              </thead>
              <tbody>
                {commissions.map((c) => (
                  <tr key={c.id} className="border-t border-border hover:bg-surface">
                    <td className="px-4 py-2 font-mono text-xs text-muted">{c.referral_id}</td>
                    <td className="px-4 py-2 text-foreground">{providerName(c.provider_id)}</td>
                    <td className="px-4 py-2 text-muted">{c.basis}</td>
                    <td className="px-4 py-2 text-muted">
                      {c.computed_amount ?? c.flat_amount ?? "—"} {c.currency}
                    </td>
                    <td className="px-4 py-2">
                      <Badge status={c.status}>{c.status}</Badge>
                    </td>
                    <td className="px-4 py-2">
                      <div className="flex flex-wrap gap-1">
                        {(NEXT_STATUS[c.status] || []).map((next) => (
                          <button
                            key={next}
                            disabled={busyId === c.id}
                            onClick={() => handleStatusChange(c.id, next)}
                            className="rounded-full border border-border-strong px-2 py-0.5 text-xs text-muted hover:border-foreground hover:text-foreground disabled:opacity-50"
                          >
                            {next}
                          </button>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {showCreate && token && (
        <CreateCommissionModal
          token={token}
          referrals={referrals}
          providers={providers}
          onClose={() => setShowCreate(false)}
          onCreated={load}
        />
      )}
    </AppShell>
  );
}

function CreateCommissionModal({
  token,
  referrals,
  providers,
  onClose,
  onCreated,
}: {
  token: string;
  referrals: ReferralRow[];
  providers: MedicalTourismProvider[];
  onClose: () => void;
  onCreated: () => void;
}) {
  const [referralId, setReferralId] = useState("");
  const [providerId, setProviderId] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [basis, setBasis] = useState<"PERCENTAGE" | "FLAT">("PERCENTAGE");
  const [commissionPercentage, setCommissionPercentage] = useState("");
  const [flatAmount, setFlatAmount] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!referralId) {
      setError("Select a referral first.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await createReferralCommission(token, {
        referral_id: referralId,
        currency,
        provider_id: providerId || undefined,
        basis,
        commission_percentage: basis === "PERCENTAGE" ? commissionPercentage || undefined : undefined,
        flat_amount: basis === "FLAT" ? flatAmount || undefined : undefined,
      });
      onCreated();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create commission.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title="New referral commission" onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-3">
        <select required value={referralId} onChange={(e) => setReferralId(e.target.value)} className="klaros-input">
          <option value="">Select referral...</option>
          {referrals.map((r) => (
            <option key={r.id} value={r.id}>
              {r.id.slice(0, 8)}… — {r.status}
              {r.revenue_amount ? ` (revenue ${r.revenue_amount})` : ""}
            </option>
          ))}
        </select>
        <select value={providerId} onChange={(e) => setProviderId(e.target.value)} className="klaros-input">
          <option value="">Provider (optional)...</option>
          {providers.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <input
          required
          placeholder="Currency (ISO-3, e.g. USD)"
          value={currency}
          maxLength={3}
          onChange={(e) => setCurrency(e.target.value.toUpperCase())}
          className="klaros-input"
        />
        <select value={basis} onChange={(e) => setBasis(e.target.value as "PERCENTAGE" | "FLAT")} className="klaros-input">
          <option value="PERCENTAGE">Percentage of referral revenue</option>
          <option value="FLAT">Flat amount</option>
        </select>
        {basis === "PERCENTAGE" ? (
          <input
            placeholder="Commission percentage (e.g. 10)"
            value={commissionPercentage}
            onChange={(e) => setCommissionPercentage(e.target.value)}
            className="klaros-input"
          />
        ) : (
          <input
            placeholder="Flat amount"
            value={flatAmount}
            onChange={(e) => setFlatAmount(e.target.value)}
            className="klaros-input"
          />
        )}
        {error && <p className="text-sm text-danger">{error}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <button type="button" onClick={onClose} className="klaros-btn-secondary">
            Cancel
          </button>
          <button type="submit" disabled={submitting} className="klaros-btn-primary">
            {submitting ? "Creating..." : "Create commission"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
