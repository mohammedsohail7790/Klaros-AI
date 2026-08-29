"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  Customer,
  CustomerHealth,
  FeedbackRow,
  Invoice,
  RetentionOpportunityRow,
  ReviewRequestRow,
  ServiceReminderRow,
  TimelineEntry,
  createCustomerNote,
  getCustomer,
  getCustomerHealth,
  getCustomerSummary,
  getCustomerTimeline,
  listFeedback,
  listInvoices,
  listReviewRequests,
  listRetentionOpportunities,
  listServiceReminders,
} from "@/lib/api";

export default function CustomerDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { token, user, loading: authLoading } = useAuth();

  const [customer, setCustomer] = useState<Customer | null>(null);
  const [timeline, setTimeline] = useState<TimelineEntry[]>([]);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [summary, setSummary] = useState<string | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [noteSubmitting, setNoteSubmitting] = useState(false);

  const [health, setHealth] = useState<CustomerHealth | null>(null);
  const [opportunities, setOpportunities] = useState<RetentionOpportunityRow[]>([]);
  const [reminders, setReminders] = useState<ServiceReminderRow[]>([]);
  const [reviewRequests, setReviewRequests] = useState<ReviewRequestRow[]>([]);
  const [feedback, setFeedback] = useState<FeedbackRow[]>([]);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [customerResult, timelineResult, invoicesResult, healthResult, opportunitiesResult, remindersResult, reviewRequestsResult, feedbackResult] =
        await Promise.all([
          getCustomer(token, id),
          getCustomerTimeline(token, id),
          listInvoices(token, { customer_id: id }),
          getCustomerHealth(token, id),
          listRetentionOpportunities(token, "OPEN"),
          listServiceReminders(token),
          listReviewRequests(token),
          listFeedback(token),
        ]);
      setCustomer(customerResult.customer);
      setTimeline(timelineResult.entries);
      setInvoices(invoicesResult.invoices);
      setHealth(healthResult);
      setOpportunities(opportunitiesResult.opportunities.filter((o) => o.customer_id === id));
      setReminders(remindersResult.reminders.filter((r) => r.customer_id === id));
      setReviewRequests(reviewRequestsResult.review_requests.filter((r) => r.customer_id === id));
      setFeedback(feedbackResult.feedback.filter((f) => f.customer_id === id));
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

  type RetentionTimelineEntry = { type: string; summary: string; timestamp: string };
  const retentionTimeline: RetentionTimelineEntry[] = [
    ...opportunities.map((o) => ({ type: "opportunity", summary: `Retention opportunity opened: ${o.reason}`, timestamp: o.detected_at })),
    ...reminders.map((r) => ({ type: "reminder", summary: `Service reminder scheduled: ${r.reason ?? r.service_type ?? "service"}`, timestamp: r.reminder_date })),
    ...reviewRequests
      .filter((r) => r.requested_at)
      .map((r) => ({ type: "review_request", summary: `Review request ${r.status.toLowerCase()} via ${r.channel}`, timestamp: r.requested_at as string })),
    ...feedback.map((f) => ({
      type: "feedback",
      summary: `Feedback received: rating ${f.rating ?? "n/a"}/5 (${f.sentiment ?? "no sentiment"})`,
      timestamp: f.received_at,
    })),
  ].sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());

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
                <h2 className="mb-3 text-sm font-medium text-neutral-300">Invoices / Payments</h2>
                {invoices.length === 0 ? (
                  <p className="text-sm text-neutral-500">No financial history for this customer yet.</p>
                ) : (
                  <ul className="space-y-2 text-sm">
                    {invoices.map((inv) => (
                      <li key={inv.id} className="flex items-center justify-between">
                        <Link href={`/finance/invoices/${inv.id}`} className="underline hover:text-white">
                          {inv.invoice_number}
                        </Link>
                        <span className="text-neutral-500">
                          {inv.status} · ${inv.total} (${inv.amount_due} due)
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
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
              {health && (
                <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-6">
                  <h2 className="mb-3 text-sm font-medium text-neutral-300">Customer health</h2>
                  <dl className="space-y-1.5 text-sm">
                    <div className="flex justify-between">
                      <dt className="text-neutral-500">Lifecycle</dt>
                      <dd className="rounded-full border border-neutral-700 px-2 py-0.5 text-xs">{health.lifecycle_state}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-neutral-500">Jobs completed</dt>
                      <dd>{health.completed_jobs} / {health.total_jobs}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-neutral-500">Total collected</dt>
                      <dd>${health.total_collected}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-neutral-500">Open balance</dt>
                      <dd>${health.open_balance}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-neutral-500">Last service</dt>
                      <dd>{health.last_completed_job_at ? new Date(health.last_completed_job_at).toLocaleDateString() : "—"}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-neutral-500">Service frequency</dt>
                      <dd>{health.average_days_between_jobs !== null ? `~${health.average_days_between_jobs} days` : "INSUFFICIENT DATA"}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-neutral-500">Last review request</dt>
                      <dd>{health.last_review_request_at ? new Date(health.last_review_request_at).toLocaleDateString() : "None"}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-neutral-500">Last referral</dt>
                      <dd>{health.last_referral_at ? new Date(health.last_referral_at).toLocaleDateString() : "None"}</dd>
                    </div>
                  </dl>
                  {feedback.some((f) => f.sentiment === "NEGATIVE") && (
                    <p className="mt-3 rounded-md border border-red-900 bg-red-950/30 p-2 text-xs text-red-300">
                      Negative feedback on file — service recovery required.
                    </p>
                  )}
                  <div className="mt-3 text-xs text-neutral-500">
                    Next recommended action:{" "}
                    {opportunities[0] ? opportunities[0].recommended_action ?? opportunities[0].reason : "None open"}
                  </div>
                </div>
              )}

              <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-6">
                <h2 className="mb-3 text-sm font-medium text-neutral-300">Retention timeline</h2>
                {retentionTimeline.length === 0 ? (
                  <p className="text-sm text-neutral-500">No retention activity recorded yet.</p>
                ) : (
                  <ul className="space-y-3">
                    {retentionTimeline.map((entry, i) => (
                      <li key={i} className="flex gap-3 text-sm">
                        <span className="mt-0.5 rounded-full border border-neutral-700 px-2 py-0.5 text-[10px] uppercase text-neutral-500">
                          {entry.type}
                        </span>
                        <div>
                          <p>{entry.summary}</p>
                          <p className="text-xs text-neutral-600">{new Date(entry.timestamp).toLocaleString()}</p>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

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
