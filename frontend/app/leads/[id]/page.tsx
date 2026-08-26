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
        <Link href="/leads" className="text-sm text-neutral-500 hover:underline">
          ← Back to leads
        </Link>

        {authLoading || loading ? (
          <p className="mt-4 text-sm text-neutral-500">Loading lead...</p>
        ) : error ? (
          <div className="mt-4 rounded-md border border-red-900 bg-red-950/30 p-4 text-sm text-red-300">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : !lead ? (
          <p className="mt-4 text-sm text-neutral-500">Lead not found.</p>
        ) : (
          <div className="mt-4 grid grid-cols-1 gap-6 lg:grid-cols-3">
            <section className="lg:col-span-2 space-y-6">
              <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-6">
                <div className="flex items-start justify-between">
                  <div>
                    <h1 className="text-xl font-semibold">{lead.name}</h1>
                    <p className="text-sm text-neutral-500">
                      {lead.source} · {lead.email ?? "no email"} · {lead.phone ?? "no phone"}
                    </p>
                  </div>
                  <select
                    value={lead.status}
                    onChange={(e) => handleStatusChange(e.target.value)}
                    className="rounded-md border border-neutral-700 bg-neutral-900 px-2 py-1 text-sm"
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
                    <dt className="text-neutral-500">Service requested</dt>
                    <dd>{lead.service_requested ?? "—"}</dd>
                  </div>
                  <div>
                    <dt className="text-neutral-500">Location</dt>
                    <dd>{lead.location ?? "—"}</dd>
                  </div>
                  <div>
                    <dt className="text-neutral-500">Urgency</dt>
                    <dd>{lead.urgency}</dd>
                  </div>
                  <div>
                    <dt className="text-neutral-500">Estimated value</dt>
                    <dd>{lead.estimated_value != null ? `$${lead.estimated_value.toLocaleString()}` : "—"}</dd>
                  </div>
                </dl>
              </div>

              {lead.customer_id && (
                <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-4 text-sm">
                  Linked to customer —{" "}
                  <Link href={`/customers/${lead.customer_id}`} className="underline">
                    view Customer 360
                  </Link>
                </div>
              )}

              <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-4 text-sm">
                <Link
                  href={`/calendar?lead_id=${lead.id}${lead.customer_id ? `&customer_id=${lead.customer_id}` : ""}`}
                  className="rounded-md bg-white px-3 py-1.5 font-medium text-black hover:bg-neutral-200"
                >
                  Book appointment
                </Link>
              </div>
            </section>

            <section className="space-y-4">
              <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-6">
                <h2 className="mb-2 text-sm font-medium text-neutral-300">Qualification</h2>
                <p className="text-sm text-neutral-500">
                  Status: <span className="text-neutral-200">{lead.qualification_status}</span>
                </p>
                {lead.lead_score != null && (
                  <p className="mt-1 text-2xl font-semibold">{lead.lead_score}/100</p>
                )}
                {lead.score_reason && (
                  <p className="mt-2 text-xs text-neutral-500">{lead.score_reason}</p>
                )}
                <button
                  onClick={handleQualify}
                  disabled={qualifying}
                  className="mt-4 w-full rounded-md border border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-900 disabled:opacity-50"
                >
                  {qualifying ? "Qualifying..." : "Re-run qualification"}
                </button>
                {qualifyError && <p className="mt-2 text-xs text-red-400">{qualifyError}</p>}
              </div>
            </section>
          </div>
        )}
      </div>
    </AppShell>
  );
}
