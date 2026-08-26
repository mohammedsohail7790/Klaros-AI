"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  Customer,
  TimelineEntry,
  createCustomerNote,
  getCustomer,
  getCustomerSummary,
  getCustomerTimeline,
} from "@/lib/api";

export default function CustomerDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { token, user, loading: authLoading } = useAuth();

  const [customer, setCustomer] = useState<Customer | null>(null);
  const [timeline, setTimeline] = useState<TimelineEntry[]>([]);
  const [summary, setSummary] = useState<string | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [noteSubmitting, setNoteSubmitting] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [customerResult, timelineResult] = await Promise.all([
        getCustomer(token, id),
        getCustomerTimeline(token, id),
      ]);
      setCustomer(customerResult.customer);
      setTimeline(timelineResult.entries);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Customer could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [token, id]);

  useEffect(() => {
    load();
  }, [load]);

  async function loadSummary() {
    if (!token) return;
    setSummaryLoading(true);
    setSummaryError(null);
    try {
      const result = await getCustomerSummary(token, id);
      setSummary(result.summary);
    } catch (err) {
      setSummaryError(err instanceof ApiError ? err.message : "Unable to generate summary. Retry.");
    } finally {
      setSummaryLoading(false);
    }
  }

  async function submitNote(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !note.trim()) return;
    setNoteSubmitting(true);
    try {
      await createCustomerNote(token, id, note.trim());
      setNote("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to save note.");
    } finally {
      setNoteSubmitting(false);
    }
  }

  const appointmentEntries = timeline.filter((e) => e.type === "appointment");

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <Link href="/customers" className="text-sm text-neutral-500 hover:underline">
          ← Back to customers
        </Link>

        {authLoading || loading ? (
          <p className="mt-4 text-sm text-neutral-500">Loading customer...</p>
        ) : error ? (
          <div className="mt-4 rounded-md border border-red-900 bg-red-950/30 p-4 text-sm text-red-300">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : !customer ? (
          <p className="mt-4 text-sm text-neutral-500">Customer not found.</p>
        ) : (
          <div className="mt-4 grid grid-cols-1 gap-6 lg:grid-cols-3">
            <section className="lg:col-span-2 space-y-6">
              <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-6">
                <h1 className="text-xl font-semibold">{customer.name}</h1>
                <p className="text-sm text-neutral-500">
                  {customer.email ?? "no email"} · {customer.phone ?? "no phone"}
                </p>
                {customer.address && (
                  <p className="mt-1 text-sm text-neutral-500">
                    {customer.address}, {customer.city} {customer.state} {customer.postal_code}
                  </p>
                )}
                <span className="mt-2 inline-block rounded-full border border-neutral-700 px-2 py-0.5 text-xs">
                  {customer.status}
                </span>
              </div>

              <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-6">
                <h2 className="mb-3 text-sm font-medium text-neutral-300">Timeline</h2>
                {timeline.length === 0 ? (
                  <p className="text-sm text-neutral-500">No activity recorded yet.</p>
                ) : (
                  <ul className="space-y-3">
                    {timeline.map((entry, i) => (
                      <li key={i} className="flex gap-3 text-sm">
                        <span className="mt-0.5 rounded-full border border-neutral-700 px-2 py-0.5 text-[10px] uppercase text-neutral-500">
                          {entry.type}
                        </span>
                        <div>
                          <p>{entry.summary}</p>
                          <p className="text-xs text-neutral-600">
                            {new Date(entry.timestamp).toLocaleString()}
                          </p>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-6">
                <h2 className="mb-3 text-sm font-medium text-neutral-300">
                  Appointments ({appointmentEntries.length})
                </h2>
                {appointmentEntries.length === 0 ? (
                  <p className="text-sm text-neutral-500">No appointments yet.</p>
                ) : (
                  <ul className="space-y-2 text-sm">
                    {appointmentEntries.map((a, i) => (
                      <li key={i} className="text-neutral-300">
                        {a.summary}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-6">
                <h2 className="mb-3 text-sm font-medium text-neutral-300">Quotes / Invoices / Payments</h2>
                <p className="text-sm text-neutral-500">Finance module not connected yet.</p>
              </div>

              <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-6">
                <h2 className="mb-3 text-sm font-medium text-neutral-300">Add note</h2>
                <form onSubmit={submitNote} className="flex gap-2">
                  <input
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    placeholder="Write a note..."
                    className="flex-1 rounded-md border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm"
                  />
                  <button
                    type="submit"
                    disabled={noteSubmitting || !note.trim()}
                    className="rounded-md border border-neutral-700 px-3 py-2 text-sm hover:bg-neutral-900 disabled:opacity-50"
                  >
                    {noteSubmitting ? "Saving..." : "Save"}
                  </button>
                </form>
              </div>
            </section>

            <section className="space-y-4">
              <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-6">
                <h2 className="mb-2 text-sm font-medium text-neutral-300">AI summary</h2>
                {summary ? (
                  <p className="text-sm text-neutral-300">{summary}</p>
                ) : (
                  <p className="text-sm text-neutral-500">Not generated yet.</p>
                )}
                {summaryError && <p className="mt-2 text-xs text-red-400">{summaryError}</p>}
                <button
                  onClick={loadSummary}
                  disabled={summaryLoading}
                  className="mt-4 w-full rounded-md border border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-900 disabled:opacity-50"
                >
                  {summaryLoading ? "Generating..." : "Generate summary"}
                </button>
              </div>
            </section>
          </div>
        )}
      </div>
    </AppShell>
  );
}
