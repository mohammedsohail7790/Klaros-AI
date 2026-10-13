"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Wrench } from "lucide-react";
import AppShell from "@/components/AppShell";
import { StatusSummary } from "@/components/ui/layout";
import { useAuth } from "@/lib/useAuth";
import { ApiError, Customer, Job, createJob, searchCustomers, searchJobs } from "@/lib/api";

import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { Modal } from "@/components/ui/Modal";
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
      <div className="mx-auto w-full max-w-6xl px-5 py-8 sm:px-8 sm:py-12">
        <header className="mb-6 flex items-center justify-between">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Jobs ({total})</h1>
          <button
            onClick={() => setShowCreate(true)}
            className="klaros-btn-primary"
          >
            New job
          </button>
        </header>

        <StatusSummary rows={jobs} noun="jobs" />

        <div className="mb-4 flex flex-wrap items-center gap-2">
          {STATUS_TABS.map((s) => (
            <button
              key={s}
              onClick={() => setStatus(s)}
              className={`klaros-filter-tab ${status === s ? "klaros-filter-tab-active" : "klaros-filter-tab-inactive"}`}
            >
              {s === "ALL" ? "All" : s.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase())}
            </button>
          ))}
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search job # / title..."
            className="ml-auto w-56 rounded-xl border border-border-strong bg-surface-muted px-3 py-1.5 text-sm"
          />
        </div>

        {authLoading || loading ? (
          <Skeleton />
        ) : error ? (
          <div className="rounded-xl border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : jobs.length === 0 ? (
          <EmptyState
            icon={Wrench}
            title="No jobs yet."
            action={
              <button onClick={() => setShowCreate(true)} className="klaros-btn-primary">
                New job
              </button>
            }
          />
        ) : (
          <div className="klaros-table-wrap">
            <table className="klaros-table">
              <thead>
                <tr>
                  <th className="px-5 py-4">Job #</th>
                  <th className="px-5 py-4">Title</th>
                  <th className="px-5 py-4">Status</th>
                  <th className="px-5 py-4">Priority</th>
                  <th className="px-5 py-4">Scheduled</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((j) => (
                  <tr key={j.id} className="border-t border-border hover:bg-surface">
                    <td className="px-5 py-4">
                      <Link href={`/jobs/${j.id}`} className="hover:underline">
                        {j.job_number}
                      </Link>
                    </td>
                    <td className="px-5 py-4">{j.title}</td>
                    <td className="px-5 py-4">
                      <Badge status={j.status}>{j.status}</Badge>
                    </td>
                    <td className="px-5 py-4 text-muted">{j.priority}</td>
                    <td className="px-5 py-4 text-muted">
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
    <Modal title="New job" onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-3">
        <input
          required
          placeholder="Title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="klaros-input"
        />
        <div>
          <input
            placeholder="Search customer by name/email"
            value={customerQuery}
            onChange={(e) => setCustomerQuery(e.target.value)}
            className="klaros-input"
          />
          {customerResults.length > 0 && (
            <ul className="mt-1 overflow-hidden rounded-3xl bg-surface-muted text-sm shadow-raised">
              {customerResults.map((c) => (
                <li
                  key={c.id}
                  onClick={() => {
                    setCustomerId(c.id);
                    setCustomerQuery(c.name);
                    setCustomerResults([]);
                  }}
                  className={`cursor-pointer px-3 py-2 transition-colors hover:bg-accent-soft/40 ${customerId === c.id ? "bg-accent-soft/40" : ""}`}
                >
                  {c.name} {c.email && `(${c.email})`}
                </li>
              ))}
            </ul>
          )}
        </div>
        <select value={priority} onChange={(e) => setPriority(e.target.value)} className="klaros-input">
          {["LOW", "NORMAL", "HIGH", "URGENT", "CRITICAL"].map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
        {error && <p className="text-sm text-danger">{error}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <button type="button" onClick={onClose} className="klaros-btn-secondary">
            Cancel
          </button>
          <button type="submit" disabled={submitting} className="klaros-btn-primary">
            {submitting ? "Creating..." : "Create job"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
