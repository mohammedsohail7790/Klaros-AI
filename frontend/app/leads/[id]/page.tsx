"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, getLead, Lead, qualifyLead, updateLead } from "@/lib/api";

const STATUS_OPTIONS = ["NEW", "CONTACTED", "QUALIFIED", "UNQUALIFIED", "BOOKED", "LOST", "CONVERTED"];

export default function LeadDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { token, user, loading: authLoading } = useAuth();
  const [lead, setLead] = useState<Lead | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [qualifying, setQualifying] = useState(false);
  const [qualifyError, setQualifyError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const result = await getLead(token, id);
      setLead(result.lead);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load this lead.");
    } finally {
      setLoading(false);
    }
  }, [token, id]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleQualify() {
    if (!token) return;
    setQualifying(true);
    setQualifyError(null);
    try {
      await qualifyLead(token, id);
      await load();
    } catch (err) {
      setQualifyError(err instanceof ApiError ? err.message : "Unable to qualify this lead. Retry.");
    } finally {
      setQualifying(false);
    }
  }

  async function handleStatusChange(status: string) {
    if (!token) return;
    try {
      await updateLead(token, id, { status });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to update this lead.");
    }
  }

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <Link href="/leads" className="text-sm text-muted hover:underline">
          ← Back to leads
        </Link>

        {authLoading || loading ? (
          <p className="mt-4 text-sm text-muted">Loading lead...</p>
        ) : error ? (
          <div className="mt-4 rounded-md border border-red-200 bg-red-50/30 p-4 text-sm text-red-700">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : !lead ? (
          <p className="mt-4 text-sm text-muted">Lead not found.</p>
        ) : (
          <div className="mt-4 grid grid-cols-1 gap-6 lg:grid-cols-3">
            <section className="lg:col-span-2 space-y-6">
              <div className="rounded-lg border border-border bg-surface p-6">
                <div className="flex items-start justify-between">
                  <div>
                    <h1 className="font-display text-2xl text-foreground">{lead.name}</h1>
                    <p className="text-sm text-muted">
                      {lead.source} · {lead.email ?? "no email"} · {lead.phone ?? "no phone"}
                    </p>
                  </div>
                  <select
                    value={lead.status}
                    onChange={(e) => handleStatusChange(e.target.value)}
                    className="rounded-md border border-border-strong bg-surface-muted px-2 py-1 text-sm"
                  >
                    {STATUS_OPTIONS.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                </div>

                <dl className="mt-4 grid grid-cols-2 gap-4 text-sm">
                  <div>
                    <dt className="text-muted">Service requested</dt>
                    <dd>{lead.service_requested ?? "—"}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">Location</dt>
                    <dd>{lead.location ?? "—"}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">Urgency</dt>
                    <dd>{lead.urgency}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">Estimated value</dt>
                    <dd>{lead.estimated_value != null ? `$${lead.estimated_value.toLocaleString()}` : "—"}</dd>
                  </div>
                </dl>
              </div>

              {lead.customer_id && (
                <div className="rounded-lg border border-border bg-surface p-4 text-sm">
                  Linked to customer —{" "}
                  <Link href={`/customers/${lead.customer_id}`} className="underline">
                    view Customer 360
                  </Link>
                </div>
              )}

              <div className="rounded-lg border border-border bg-surface p-4 text-sm">
                <Link
                  href={`/calendar?lead_id=${lead.id}${lead.customer_id ? `&customer_id=${lead.customer_id}` : ""}`}
                  className="rounded-md bg-surface px-3 py-1.5 font-medium text-foreground hover:bg-surface-muted"
                >
                  Book appointment
                </Link>
              </div>
            </section>

            <section className="space-y-4">
              <div className="rounded-lg border border-border bg-surface p-6">
                <h2 className="mb-2 text-sm font-medium text-muted">Qualification</h2>
                <p className="text-sm text-muted">
                  Status: <span className="text-foreground">{lead.qualification_status}</span>
                </p>
                {lead.lead_score != null && (
                  <p className="mt-1 text-2xl font-semibold">{lead.lead_score}/100</p>
                )}
                {lead.score_reason && (
                  <p className="mt-2 text-xs text-muted">{lead.score_reason}</p>
                )}
                <button
                  onClick={handleQualify}
                  disabled={qualifying}
                  className="mt-4 w-full rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
                >
                  {qualifying ? "Qualifying..." : "Re-run qualification"}
                </button>
                {qualifyError && <p className="mt-2 text-xs text-red-600">{qualifyError}</p>}
              </div>
            </section>
          </div>
        )}
      </div>
    </AppShell>
  );
}
