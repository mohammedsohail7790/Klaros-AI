"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, createLead, Lead, searchLeads } from "@/lib/api";

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
          <h1 className="text-xl font-semibold">Leads</h1>
          <button
            onClick={() => setShowCreate(true)}
            className="rounded-md bg-white px-3 py-1.5 text-sm font-medium text-black hover:bg-neutral-200"
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
                  ? "border-white bg-white text-black"
                  : "border-neutral-700 text-neutral-400 hover:border-neutral-500"
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
            className="ml-auto w-64 rounded-md border border-neutral-700 bg-neutral-900 px-3 py-1.5 text-sm"
          />
        </div>

        {authLoading || loading ? (
          <p className="text-sm text-neutral-500">Loading leads...</p>
        ) : error ? (
          <div className="rounded-md border border-red-900 bg-red-950/30 p-4 text-sm text-red-300">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : leads.length === 0 ? (
          <p className="text-sm text-neutral-500">No leads yet. Create one to get started.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-neutral-800">
            <table className="w-full text-left text-sm">
              <thead className="bg-neutral-950 text-neutral-500">
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
                  <tr key={lead.id} className="border-t border-neutral-900 hover:bg-neutral-950">
                    <td className="px-4 py-2">
                      <Link href={`/leads/${lead.id}`} className="hover:underline">
                        {lead.name}
                      </Link>
                    </td>
                    <td className="px-4 py-2 text-neutral-400">{lead.source}</td>
                    <td className="px-4 py-2 text-neutral-400">{lead.service_requested ?? "—"}</td>
                    <td className="px-4 py-2 text-neutral-400">{lead.urgency}</td>
                    <td className="px-4 py-2 text-neutral-400">{lead.lead_score ?? "—"}</td>
                    <td className="px-4 py-2">
                      <span className="rounded-full border border-neutral-700 px-2 py-0.5 text-xs">
                        {lead.status}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-neutral-500">
                      {new Date(lead.created_at).toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {!loading && !error && total > limit && (
          <div className="mt-4 flex items-center gap-3 text-sm text-neutral-400">
            <button
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - limit))}
              className="rounded border border-neutral-700 px-2 py-1 disabled:opacity-40"
            >
              Previous
            </button>
            <span>
              {offset + 1}-{Math.min(offset + limit, total)} of {total}
            </span>
            <button
              disabled={offset + limit >= total}
              onClick={() => setOffset(offset + limit)}
              className="rounded border border-neutral-700 px-2 py-1 disabled:opacity-40"
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
    <div className="fixed inset-0 flex items-center justify-center bg-black/60 px-4">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-md space-y-3 rounded-lg border border-neutral-800 bg-neutral-950 p-6"
      >
        <h2 className="text-lg font-semibold">New lead</h2>
        <input
          required
          placeholder="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="w-full rounded-md border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm"
        />
        <select
          value={source}
          onChange={(e) => setSource(e.target.value)}
          className="w-full rounded-md border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm"
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
          className="w-full rounded-md border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm"
        />
        <input
          placeholder="Phone (optional)"
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
          className="w-full rounded-md border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm"
        />
        <input
          placeholder="Service requested"
          value={serviceRequested}
          onChange={(e) => setServiceRequested(e.target.value)}
          className="w-full rounded-md border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm"
        />
        <select
          value={urgency}
          onChange={(e) => setUrgency(e.target.value)}
          className="w-full rounded-md border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm"
        >
          {["LOW", "MEDIUM", "HIGH", "EMERGENCY"].map((u) => (
            <option key={u} value={u}>
              {u}
            </option>
          ))}
        </select>

        {error && <p className="text-sm text-red-400">{error}</p>}

        <div className="flex justify-end gap-2 pt-2">
          <button type="button" onClick={onClose} className="rounded-md px-3 py-1.5 text-sm text-neutral-400">
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="rounded-md bg-white px-3 py-1.5 text-sm font-medium text-black disabled:opacity-50"
          >
            {submitting ? "Creating..." : "Create lead"}
          </button>
        </div>
      </form>
    </div>
  );
}
