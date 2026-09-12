"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, createLead, Lead, searchLeads } from "@/lib/api";

import { Badge } from "@/components/ui/Badge";
const STATUS_TABS = ["ALL", "NEW", "CONTACTED", "QUALIFIED", "BOOKED", "LOST"];

export default function LeadsPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [leads, setLeads] = useState<Lead[]>([]);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState("ALL");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const limit = 20;

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const result = await searchLeads(token, {
        status: status === "ALL" ? undefined : status,
        q: q || undefined,
        limit,
        offset,
      });
      setLeads(result.leads);
      setTotal(result.total);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load leads.");
    } finally {
      setLoading(false);
    }
  }, [token, status, q, offset]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <header className="mb-6 flex items-center justify-between">
          <h1 className="font-display text-2xl text-foreground">Leads</h1>
          <button
            onClick={() => setShowCreate(true)}
            className="klaros-btn-primary"
          >
            New lead
          </button>
        </header>

        <div className="mb-4 flex flex-wrap items-center gap-2">
          {STATUS_TABS.map((s) => (
            <button
              key={s}
              onClick={() => {
                setStatus(s);
                setOffset(0);
              }}
              className={`rounded-full border px-3 py-1 text-xs ${
                status === s
                  ? "border-foreground bg-surface text-foreground"
                  : "border-border-strong text-muted hover:border-border-strong"
              }`}
            >
              {s}
            </button>
          ))}
          <input
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setOffset(0);
            }}
            placeholder="Search name, email, service..."
            className="ml-auto w-64 rounded-md border border-border-strong bg-surface-muted px-3 py-1.5 text-sm"
          />
        </div>

        {authLoading || loading ? (
          <p className="text-sm text-muted">Loading leads...</p>
        ) : error ? (
          <div className="rounded-md border border-red-200 bg-red-50/30 p-4 text-sm text-red-700">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : leads.length === 0 ? (
          <p className="text-sm text-muted">No leads yet. Create one to get started.</p>
        ) : (
          <div className="klaros-table-wrap">
            <table className="klaros-table">
              <thead className="bg-surface text-muted">
                <tr>
                  <th className="px-4 py-2">Name</th>
                  <th className="px-4 py-2">Source</th>
                  <th className="px-4 py-2">Service</th>
                  <th className="px-4 py-2">Urgency</th>
                  <th className="px-4 py-2">Score</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2">Created</th>
                </tr>
              </thead>
              <tbody>
                {leads.map((lead) => (
                  <tr key={lead.id} className="border-t border-border hover:bg-surface">
                    <td className="px-4 py-2">
                      <Link href={`/leads/${lead.id}`} className="hover:underline">
                        {lead.name}
                      </Link>
                    </td>
                    <td className="px-4 py-2 text-muted">{lead.source}</td>
                    <td className="px-4 py-2 text-muted">{lead.service_requested ?? "—"}</td>
                    <td className="px-4 py-2 text-muted">{lead.urgency}</td>
                    <td className="px-4 py-2 text-muted">{lead.lead_score ?? "—"}</td>
                    <td className="px-4 py-2">
                      <Badge status={lead.status}>{lead.status}</Badge>
                    </td>
                    <td className="px-4 py-2 text-muted">
                      {new Date(lead.created_at).toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {!loading && !error && total > limit && (
          <div className="mt-4 flex items-center gap-3 text-sm text-muted">
            <button
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - limit))}
              className="rounded border border-border-strong px-2 py-1 disabled:opacity-40"
            >
              Previous
            </button>
            <span>
              {offset + 1}-{Math.min(offset + limit, total)} of {total}
            </span>
            <button
              disabled={offset + limit >= total}
              onClick={() => setOffset(offset + limit)}
              className="rounded border border-border-strong px-2 py-1 disabled:opacity-40"
            >
              Next
            </button>
          </div>
        )}
      </div>

      {showCreate && token && (
        <CreateLeadModal token={token} onClose={() => setShowCreate(false)} onCreated={load} />
      )}
    </AppShell>
  );
}

function CreateLeadModal({
  token,
  onClose,
  onCreated,
}: {
  token: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [source, setSource] = useState("WEB");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [serviceRequested, setServiceRequested] = useState("");
  const [urgency, setUrgency] = useState("MEDIUM");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await createLead(token, {
        name,
        source,
        email: email || undefined,
        phone: phone || undefined,
        service_requested: serviceRequested || undefined,
        urgency,
      });
      onCreated();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create lead.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/60 backdrop-blur-sm px-4">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-md space-y-3 rounded-lg border border-border bg-surface p-6"
      >
        <h2 className="font-display text-xl text-foreground">New lead</h2>
        <input
          required
          placeholder="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="w-full rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm"
        />
        <select
          value={source}
          onChange={(e) => setSource(e.target.value)}
          className="w-full rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm"
        >
          {["PHONE", "WEB", "CHAT", "TEXT", "DM", "MARKETPLACE", "REFERRAL", "WALK_IN"].map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <input
          placeholder="Email (optional)"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="w-full rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm"
        />
        <input
          placeholder="Phone (optional)"
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
          className="w-full rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm"
        />
        <input
          placeholder="Service requested"
          value={serviceRequested}
          onChange={(e) => setServiceRequested(e.target.value)}
          className="w-full rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm"
        />
        <select
          value={urgency}
          onChange={(e) => setUrgency(e.target.value)}
          className="w-full rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm"
        >
          {["LOW", "MEDIUM", "HIGH", "EMERGENCY"].map((u) => (
            <option key={u} value={u}>
              {u}
            </option>
          ))}
        </select>

        {error && <p className="text-sm text-red-600">{error}</p>}

        <div className="flex justify-end gap-2 pt-2">
          <button type="button" onClick={onClose} className="rounded-md px-3 py-1.5 text-sm text-muted">
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="rounded-md bg-surface px-3 py-1.5 text-sm font-medium text-foreground disabled:opacity-50"
          >
            {submitting ? "Creating..." : "Create lead"}
          </button>
        </div>
      </form>
    </div>
  );
}
