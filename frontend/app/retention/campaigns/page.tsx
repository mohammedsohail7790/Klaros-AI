"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { Heart } from "lucide-react";
import AppShell from "@/components/AppShell";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { useAuth } from "@/lib/useAuth";
import { useToast } from "@/components/ui/Toast";
import {
  ApiError,
  Customer,
  RetentionCampaignRow,
  createRetentionCampaign,
  enrollCustomerInRetentionCampaign,
  executeDueRetentionActivities,
  listRetentionCampaigns,
  searchCustomers,
  setRetentionCampaignStatus,
} from "@/lib/api";

const TYPES = [
  "POST_JOB_FOLLOWUP",
  "SERVICE_REMINDER",
  "WIN_BACK",
  "REVIEW_REQUEST",
  "REFERRAL_INVITE",
  "VIP_CUSTOMER",
  "SERVICE_RECOVERY",
];
const STATUSES = ["DRAFT", "PENDING_APPROVAL", "APPROVED", "ACTIVE", "PAUSED", "COMPLETED", "CANCELLED"];

export default function RetentionCampaignsPage() {
  const toast = useToast();
  const { token, user, loading: authLoading } = useAuth();
  const [campaigns, setCampaigns] = useState<RetentionCampaignRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [type, setType] = useState(TYPES[0]);
  const [submitting, setSubmitting] = useState(false);

  const [enrollingCampaignId, setEnrollingCampaignId] = useState<string | null>(null);
  const [customerQuery, setCustomerQuery] = useState("");
  const [customerResults, setCustomerResults] = useState<Customer[]>([]);
  const [enrolling, setEnrolling] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      setCampaigns((await listRetentionCampaigns(token)).campaigns);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load retention campaigns.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!token || !customerQuery) {
      setCustomerResults([]);
      return;
    }
    const handle = setTimeout(() => {
      searchCustomers(token, { q: customerQuery, limit: 5 })
        .then((r) => setCustomerResults(r.customers))
        .catch(() => setCustomerResults([]));
    }, 300);
    return () => clearTimeout(handle);
  }, [customerQuery, token]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !name.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      await createRetentionCampaign(token, name.trim(), type);
      setName("");
      setShowCreate(false);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to create campaign.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleStatusChange(campaignId: string, status: string) {
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      await setRetentionCampaignStatus(token, campaignId, status);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to update status.");
    } finally {
      setBusy(false);
    }
  }

  async function handleEnroll(customerId: string, customerName: string) {
    if (!token || !enrollingCampaignId) return;
    setEnrolling(true);
    setError(null);
    try {
      await enrollCustomerInRetentionCampaign(token, enrollingCampaignId, customerId);
      toast.success(`${customerName} enrolled.`);
      setEnrollingCampaignId(null);
      setCustomerQuery("");
      setCustomerResults([]);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to enroll customer.");
    } finally {
      setEnrolling(false);
    }
  }

  async function handleExecuteDue() {
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      const result = await executeDueRetentionActivities(token);
      toast.success(`${result.executed_activity_ids.length} activity(ies) executed.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to execute due activities.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <div className="mb-6 flex items-center justify-between">
          <h1 className="font-display text-2xl text-foreground">Retention Campaigns</h1>
          <div className="flex gap-2">
            <button
              disabled={busy}
              onClick={handleExecuteDue}
              className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
            >
              Execute due activities
            </button>
            <button onClick={() => setShowCreate((v) => !v)} className="klaros-btn-primary">
              New campaign
            </button>
          </div>
        </div>

        <p className="mb-4 text-xs text-muted">
          Win-back, post-job follow-up, VIP, and other automated customer touchpoints — enrolling a customer
          schedules a real activity; sending only ever reaches the internal test communication provider until a
          real one is connected.
        </p>

        {error && (
          <div className="mb-4 rounded-md border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">{error}</div>
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
              <label className="block text-xs text-muted">Type</label>
              <select
                value={type}
                onChange={(e) => setType(e.target.value)}
                className="rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
              >
                {TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </div>
            <button
              type="submit"
              disabled={submitting || !name.trim()}
              className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
            >
              {submitting ? "Creating..." : "Create"}
            </button>
          </form>
        )}

        {authLoading || loading ? (
          <Skeleton />
        ) : campaigns.length === 0 ? (
          <EmptyState icon={Heart} title="No retention campaigns yet." />
        ) : (
          <div className="klaros-table-wrap">
            <table className="klaros-table">
              <thead className="bg-surface text-muted">
                <tr>
                  <th className="px-4 py-2">Name</th>
                  <th className="px-4 py-2">Type</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {campaigns.map((c) => (
                  <Fragment key={c.id}>
                    <tr className="border-t border-border">
                      <td className="px-4 py-2">{c.name}</td>
                      <td className="px-4 py-2 text-muted">{c.type}</td>
                      <td className="px-4 py-2">
                        <div className="flex items-center gap-2">
                          <Badge status={c.status}>{c.status}</Badge>
                          <select
                            value={c.status}
                            disabled={busy}
                            onChange={(e) => handleStatusChange(c.id, e.target.value)}
                            className="rounded-md border border-border-strong bg-surface-muted px-1.5 py-0.5 text-xs disabled:opacity-50"
                          >
                            {STATUSES.map((s) => (
                              <option key={s} value={s}>
                                {s}
                              </option>
                            ))}
                          </select>
                        </div>
                      </td>
                      <td className="px-4 py-2">
                        <button
                          onClick={() => {
                            setEnrollingCampaignId(enrollingCampaignId === c.id ? null : c.id);
                            setCustomerQuery("");
                            setCustomerResults([]);
                          }}
                          className="text-xs underline text-muted hover:text-foreground"
                        >
                          Enroll customer
                        </button>
                      </td>
                    </tr>
                    {enrollingCampaignId === c.id && (
                      <tr className="border-t border-border bg-surface-muted">
                        <td colSpan={4} className="px-4 py-3">
                          <div className="relative max-w-sm">
                            <input
                              value={customerQuery}
                              onChange={(e) => setCustomerQuery(e.target.value)}
                              placeholder="Search customer by name/email"
                              disabled={enrolling}
                              className="w-full rounded-md border border-border-strong bg-surface px-3 py-1.5 text-sm disabled:opacity-50"
                            />
                            {customerResults.length > 0 && (
                              <ul className="absolute z-10 mt-1 w-full rounded-md border border-border bg-surface text-sm shadow-popover">
                                {customerResults.map((cust) => (
                                  <li
                                    key={cust.id}
                                    onClick={() => handleEnroll(cust.id, cust.name)}
                                    className="cursor-pointer px-3 py-2 hover:bg-surface-muted"
                                  >
                                    {cust.name} {cust.email && `(${cust.email})`}
                                  </li>
                                ))}
                              </ul>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </AppShell>
  );
}
