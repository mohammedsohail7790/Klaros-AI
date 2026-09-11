"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, Customer, Job, createJob, searchCustomers, searchJobs } from "@/lib/api";

const STATUS_TABS = [
  "ALL", "DRAFT", "SCHEDULED", "DISPATCHED", "EN_ROUTE", "ON_SITE",
  "IN_PROGRESS", "BLOCKED", "QA_PENDING", "COMPLETED", "CLOSED",
];

export default function JobsPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState("ALL");
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const result = await searchJobs(token, { status: status === "ALL" ? undefined : status, q: q || undefined, limit: 50 });
      setJobs(result.jobs);
      setTotal(result.total);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load jobs.");
    } finally {
      setLoading(false);
    }
  }, [token, status, q]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <header className="mb-6 flex items-center justify-between">
          <h1 className="text-xl font-semibold">Jobs ({total})</h1>
          <button
            onClick={() => setShowCreate(true)}
            className="rounded-md bg-surface px-3 py-1.5 text-sm font-medium text-foreground hover:bg-surface-muted"
          >
            New job
          </button>
        </header>

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
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search job # / title..."
            className="ml-auto w-56 rounded-md border border-border-strong bg-surface-muted px-3 py-1.5 text-sm"
          />
        </div>

        {authLoading || loading ? (
          <p className="text-sm text-muted">Loading jobs...</p>
        ) : error ? (
          <div className="rounded-md border border-red-200 bg-red-50/30 p-4 text-sm text-red-700">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : jobs.length === 0 ? (
          <p className="text-sm text-muted">No jobs yet.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-left text-sm">
              <thead className="bg-surface text-muted">
                <tr>
                  <th className="px-4 py-2">Job #</th>
                  <th className="px-4 py-2">Title</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2">Priority</th>
                  <th className="px-4 py-2">Scheduled</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((j) => (
                  <tr key={j.id} className="border-t border-border hover:bg-surface">
                    <td className="px-4 py-2">
                      <Link href={`/jobs/${j.id}`} className="hover:underline">
                        {j.job_number}
                      </Link>
                    </td>
                    <td className="px-4 py-2">{j.title}</td>
                    <td className="px-4 py-2">
                      <span className="rounded-full border border-border-strong px-2 py-0.5 text-xs">{j.status}</span>
                    </td>
                    <td className="px-4 py-2 text-muted">{j.priority}</td>
                    <td className="px-4 py-2 text-muted">
                      {j.scheduled_start ? new Date(j.scheduled_start).toLocaleString() : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {showCreate && token && <CreateJobModal token={token} onClose={() => setShowCreate(false)} onCreated={load} />}
    </AppShell>
  );
}

function CreateJobModal({ token, onClose, onCreated }: { token: string; onClose: () => void; onCreated: () => void }) {
  const [title, setTitle] = useState("");
  const [customerQuery, setCustomerQuery] = useState("");
  const [customerResults, setCustomerResults] = useState<Customer[]>([]);
  const [customerId, setCustomerId] = useState("");
  const [priority, setPriority] = useState("NORMAL");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!customerQuery) {
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

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!customerId) {
      setError("Select a customer first.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await createJob(token, { title, customer_id: customerId, priority });
      onCreated();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create job.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 flex items-center justify-center bg-background/60 px-4">
      <form onSubmit={handleSubmit} className="w-full max-w-md space-y-3 rounded-lg border border-border bg-surface p-6">
        <h2 className="text-lg font-semibold">New job</h2>
        <input
          required
          placeholder="Title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="w-full rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm"
        />
        <div>
          <input
            placeholder="Search customer by name/email"
            value={customerQuery}
            onChange={(e) => setCustomerQuery(e.target.value)}
            className="w-full rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm"
          />
          {customerResults.length > 0 && (
            <ul className="mt-1 rounded-md border border-border bg-surface-muted text-sm">
              {customerResults.map((c) => (
                <li
                  key={c.id}
                  onClick={() => {
                    setCustomerId(c.id);
                    setCustomerQuery(c.name);
                    setCustomerResults([]);
                  }}
                  className={`cursor-pointer px-3 py-2 hover:bg-surface-muted ${customerId === c.id ? "bg-surface-muted" : ""}`}
                >
                  {c.name} {c.email && `(${c.email})`}
                </li>
              ))}
            </ul>
          )}
        </div>
        <select
          value={priority}
          onChange={(e) => setPriority(e.target.value)}
          className="w-full rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm"
        >
          {["LOW", "NORMAL", "HIGH", "URGENT", "CRITICAL"].map((p) => (
            <option key={p} value={p}>
              {p}
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
            {submitting ? "Creating..." : "Create job"}
          </button>
        </div>
      </form>
    </div>
  );
}
