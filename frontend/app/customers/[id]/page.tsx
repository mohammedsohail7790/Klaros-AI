"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Calendar, Clock, Heart, Receipt } from "lucide-react";
import AppShell from "@/components/AppShell";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  Customer,
  CustomerHealth,
  FeedbackRow,
  Invoice,
  Payment,
  RetentionOpportunityRow,
  ReviewRequestRow,
  ServiceReminderRow,
  TimelineEntry,
  createCustomerNote,
  createRefundRequest,
  getCustomer,
  getCustomerHealth,
  getCustomerSummary,
  getCustomerTimeline,
  listFeedback,
  listInvoices,
  listPayments,
  listReviewRequests,
  listRetentionOpportunities,
  listServiceReminders,
  updateCustomer,
} from "@/lib/api";

const CUSTOMER_STATUSES = ["PROSPECT", "ACTIVE", "INACTIVE"];

export default function CustomerDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { token, user, loading: authLoading } = useAuth();

  const [customer, setCustomer] = useState<Customer | null>(null);
  const [timeline, setTimeline] = useState<TimelineEntry[]>([]);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [payments, setPayments] = useState<Payment[]>([]);
  const [summary, setSummary] = useState<string | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [noteSubmitting, setNoteSubmitting] = useState(false);
  const [editingCustomer, setEditingCustomer] = useState(false);
  const [editName, setEditName] = useState("");
  const [editEmail, setEditEmail] = useState("");
  const [editPhone, setEditPhone] = useState("");
  const [editAddress, setEditAddress] = useState("");
  const [customerSaving, setCustomerSaving] = useState(false);
  const [refundingPaymentId, setRefundingPaymentId] = useState<string | null>(null);
  const [refundAmount, setRefundAmount] = useState("");
  const [refundReason, setRefundReason] = useState("");
  const [refundBusy, setRefundBusy] = useState(false);
  const [refundNotice, setRefundNotice] = useState<string | null>(null);

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
      const [customerResult, timelineResult, invoicesResult, paymentsResult, healthResult, opportunitiesResult, remindersResult, reviewRequestsResult, feedbackResult] =
        await Promise.all([
          getCustomer(token, id),
          getCustomerTimeline(token, id),
          listInvoices(token, { customer_id: id }),
          listPayments(token, id),
          getCustomerHealth(token, id),
          listRetentionOpportunities(token, "OPEN", id),
          listServiceReminders(token, id),
          listReviewRequests(token, id),
          listFeedback(token, undefined, id),
        ]);
      setCustomer(customerResult.customer);
      setTimeline(timelineResult.entries);
      setInvoices(invoicesResult.invoices);
      setPayments(paymentsResult.payments);
      setHealth(healthResult);
      setOpportunities(opportunitiesResult.opportunities);
      setReminders(remindersResult.reminders);
      setReviewRequests(reviewRequestsResult.review_requests);
      setFeedback(feedbackResult.feedback);
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

  function startEditingCustomer() {
    if (!customer) return;
    setEditName(customer.name);
    setEditEmail(customer.email ?? "");
    setEditPhone(customer.phone ?? "");
    setEditAddress(customer.address ?? "");
    setEditingCustomer(true);
  }

  async function handleSaveCustomer(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !editName.trim()) return;
    setCustomerSaving(true);
    setError(null);
    try {
      await updateCustomer(token, id, {
        name: editName.trim(),
        email: editEmail.trim() || undefined,
        phone: editPhone.trim() || undefined,
        address: editAddress.trim() || undefined,
      });
      setEditingCustomer(false);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to save customer.");
    } finally {
      setCustomerSaving(false);
    }
  }

  async function handleStatusChange(status: string) {
    if (!token) return;
    setError(null);
    try {
      await updateCustomer(token, id, { status });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to update status.");
    }
  }

  function openRefundForm(payment: Payment) {
    setRefundingPaymentId(payment.id);
    setRefundAmount(payment.amount);
    setRefundReason("");
    setRefundNotice(null);
  }

  async function submitRefund(e: React.FormEvent, paymentId: string) {
    e.preventDefault();
    if (!token || !refundAmount || !refundReason.trim()) return;
    setRefundBusy(true);
    setError(null);
    try {
      await createRefundRequest(token, { payment_id: paymentId, amount: refundAmount, reason: refundReason.trim() });
      setRefundNotice("Refund requested — pending approval.");
      setRefundingPaymentId(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to request refund.");
    } finally {
      setRefundBusy(false);
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
        <Link href="/customers" className="text-sm text-muted hover:underline">
          ← Back to customers
        </Link>

        {authLoading || loading ? (
          <Skeleton />
        ) : error ? (
          <div className="mt-4 rounded-md border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : !customer ? (
          <p className="mt-4 text-sm text-muted">Customer not found.</p>
        ) : (
          <div className="mt-4 grid grid-cols-1 gap-6 lg:grid-cols-3">
            <section className="lg:col-span-2 space-y-6">
              <div className="rounded-lg border border-border bg-surface p-6">
                {editingCustomer ? (
                  <form onSubmit={handleSaveCustomer} className="space-y-2">
                    <div>
                      <label className="block text-xs text-muted">Name</label>
                      <input
                        value={editName}
                        onChange={(e) => setEditName(e.target.value)}
                        required
                        className="w-full rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                      />
                    </div>
                    <div className="flex gap-2">
                      <div className="flex-1">
                        <label className="block text-xs text-muted">Email</label>
                        <input
                          value={editEmail}
                          onChange={(e) => setEditEmail(e.target.value)}
                          className="w-full rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                        />
                      </div>
                      <div className="flex-1">
                        <label className="block text-xs text-muted">Phone</label>
                        <input
                          value={editPhone}
                          onChange={(e) => setEditPhone(e.target.value)}
                          className="w-full rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                        />
                      </div>
                    </div>
                    <div>
                      <label className="block text-xs text-muted">Address</label>
                      <input
                        value={editAddress}
                        onChange={(e) => setEditAddress(e.target.value)}
                        className="w-full rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                      />
                    </div>
                    <div className="flex gap-2 pt-1">
                      <button
                        type="submit"
                        disabled={customerSaving || !editName.trim()}
                        className="klaros-btn-primary disabled:opacity-50"
                      >
                        {customerSaving ? "Saving..." : "Save"}
                      </button>
                      <button
                        type="button"
                        onClick={() => setEditingCustomer(false)}
                        className="rounded-md px-3 py-1.5 text-sm text-muted hover:underline"
                      >
                        Cancel
                      </button>
                    </div>
                  </form>
                ) : (
                  <>
                    <div className="flex items-start justify-between">
                      <h1 className="font-display text-2xl text-foreground">{customer.name}</h1>
                      <button
                        onClick={startEditingCustomer}
                        className="text-xs text-muted underline hover:text-foreground"
                      >
                        Edit
                      </button>
                    </div>
                    <p className="text-sm text-muted">
                      {customer.email ?? "no email"} · {customer.phone ?? "no phone"}
                    </p>
                    {customer.address && (
                      <p className="mt-1 text-sm text-muted">
                        {customer.address}, {customer.city} {customer.state} {customer.postal_code}
                      </p>
                    )}
                    <div className="mt-2 flex items-center gap-2">
                      <Badge status={customer.status}>{customer.status}</Badge>
                      <select
                        value={customer.status}
                        onChange={(e) => handleStatusChange(e.target.value)}
                        className="rounded-md border border-border-strong bg-surface-muted px-2 py-1 text-xs"
                      >
                        {CUSTOMER_STATUSES.map((s) => (
                          <option key={s} value={s}>
                            {s}
                          </option>
                        ))}
                      </select>
                    </div>
                  </>
                )}
              </div>

              <div className="rounded-lg border border-border bg-surface p-6">
                <h2 className="mb-3 text-sm font-medium text-muted">Timeline</h2>
                {timeline.length === 0 ? (
                  <EmptyState icon={Clock} title="No activity recorded yet." compact />
                ) : (
                  <ul className="space-y-3">
                    {timeline.map((entry, i) => (
                      <li key={i} className="flex gap-3 text-sm">
                        <span className="mt-0.5 rounded-full border border-border-strong px-2 py-0.5 text-[10px] uppercase text-muted">
                          {entry.type}
                        </span>
                        <div>
                          <p>{entry.summary}</p>
                          <p className="text-xs text-muted-foreground">
                            {new Date(entry.timestamp).toLocaleString()}
                          </p>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="rounded-lg border border-border bg-surface p-6">
                <h2 className="mb-3 text-sm font-medium text-muted">
                  Appointments ({appointmentEntries.length})
                </h2>
                {appointmentEntries.length === 0 ? (
                  <EmptyState icon={Calendar} title="No appointments yet." compact />
                ) : (
                  <ul className="space-y-2 text-sm">
                    {appointmentEntries.map((a, i) => (
                      <li key={i} className="text-muted">
                        {a.summary}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="rounded-lg border border-border bg-surface p-6">
                <h2 className="mb-3 text-sm font-medium text-muted">Invoices / Payments</h2>
                {invoices.length === 0 ? (
                  <EmptyState icon={Receipt} title="No financial history for this customer yet." compact />
                ) : (
                  <ul className="space-y-2 text-sm">
                    {invoices.map((inv) => (
                      <li key={inv.id} className="flex items-center justify-between">
                        <Link href={`/finance/invoices/${inv.id}`} className="underline hover:text-foreground">
                          {inv.invoice_number}
                        </Link>
                        <span className="text-muted">
                          {inv.status} · ${inv.total} (${inv.amount_due} due)
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="rounded-lg border border-border bg-surface p-6">
                <h2 className="mb-3 text-sm font-medium text-muted">Payments received</h2>
                {refundNotice && <p className="mb-2 text-sm text-success">{refundNotice}</p>}
                {payments.length === 0 ? (
                  <EmptyState icon={Receipt} title="No payments recorded for this customer yet." compact />
                ) : (
                  <ul className="space-y-2 text-sm">
                    {payments.map((p) => (
                      <li key={p.id}>
                        <div className="flex items-center justify-between">
                          <span>
                            ${p.amount} · {p.payment_method ?? p.provider}
                          </span>
                          <span className="flex items-center gap-2 text-muted">
                            {p.status} · {new Date(p.received_at).toLocaleDateString()}
                            {p.status === "SUCCEEDED" && (
                              <button
                                onClick={() => openRefundForm(p)}
                                className="text-xs text-danger underline hover:text-foreground"
                              >
                                Request refund
                              </button>
                            )}
                          </span>
                        </div>
                        {refundingPaymentId === p.id && (
                          <form
                            onSubmit={(e) => submitRefund(e, p.id)}
                            className="mt-2 flex flex-wrap items-end gap-2 rounded-md border border-border-strong bg-surface-muted p-3"
                          >
                            <div>
                              <label className="block text-xs text-muted">Amount</label>
                              <input
                                value={refundAmount}
                                onChange={(e) => setRefundAmount(e.target.value)}
                                required
                                className="w-24 rounded-md border border-border-strong bg-surface px-2 py-1 text-sm"
                              />
                            </div>
                            <div className="flex-1">
                              <label className="block text-xs text-muted">Reason</label>
                              <input
                                value={refundReason}
                                onChange={(e) => setRefundReason(e.target.value)}
                                required
                                className="w-full rounded-md border border-border-strong bg-surface px-2 py-1 text-sm"
                              />
                            </div>
                            <button
                              type="submit"
                              disabled={refundBusy}
                              className="rounded-md border border-border-strong px-3 py-1 text-xs hover:bg-surface disabled:opacity-50"
                            >
                              Submit
                            </button>
                            <button
                              type="button"
                              onClick={() => setRefundingPaymentId(null)}
                              className="text-xs text-muted hover:underline"
                            >
                              Cancel
                            </button>
                          </form>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="rounded-lg border border-border bg-surface p-6">
                <h2 className="mb-3 text-sm font-medium text-muted">Add note</h2>
                <form onSubmit={submitNote} className="flex gap-2">
                  <input
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    placeholder="Write a note..."
                    className="flex-1 rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm"
                  />
                  <button
                    type="submit"
                    disabled={noteSubmitting || !note.trim()}
                    className="rounded-md border border-border-strong px-3 py-2 text-sm hover:bg-surface-muted disabled:opacity-50"
                  >
                    {noteSubmitting ? "Saving..." : "Save"}
                  </button>
                </form>
              </div>
            </section>

            <section className="space-y-4">
              {health && (
                <div className="rounded-lg border border-border bg-surface p-6">
                  <h2 className="mb-3 text-sm font-medium text-muted">Customer health</h2>
                  <dl className="space-y-1.5 text-sm">
                    <div className="flex justify-between">
                      <dt className="text-muted">Lifecycle</dt>
                      <dd><Badge status={health.lifecycle_state}>{health.lifecycle_state}</Badge></dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-muted">Jobs completed</dt>
                      <dd>{health.completed_jobs} / {health.total_jobs}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-muted">Total collected</dt>
                      <dd>${health.total_collected}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-muted">Open balance</dt>
                      <dd>${health.open_balance}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-muted">Last service</dt>
                      <dd>{health.last_completed_job_at ? new Date(health.last_completed_job_at).toLocaleDateString() : "—"}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-muted">Service frequency</dt>
                      <dd>{health.average_days_between_jobs !== null ? `~${health.average_days_between_jobs} days` : "INSUFFICIENT DATA"}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-muted">Last review request</dt>
                      <dd>{health.last_review_request_at ? new Date(health.last_review_request_at).toLocaleDateString() : "None"}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-muted">Last referral</dt>
                      <dd>{health.last_referral_at ? new Date(health.last_referral_at).toLocaleDateString() : "None"}</dd>
                    </div>
                  </dl>
                  {feedback.some((f) => f.sentiment === "NEGATIVE") && (
                    <p className="mt-3 rounded-md border border-danger/25 bg-danger/[0.06] p-2 text-xs text-danger">
                      Negative feedback on file — service recovery required.
                    </p>
                  )}
                  <div className="mt-3 text-xs text-muted">
                    Next recommended action:{" "}
                    {opportunities[0] ? opportunities[0].recommended_action ?? opportunities[0].reason : "None open"}
                  </div>
                </div>
              )}

              <div className="rounded-lg border border-border bg-surface p-6">
                <h2 className="mb-3 text-sm font-medium text-muted">Retention timeline</h2>
                {retentionTimeline.length === 0 ? (
                  <EmptyState icon={Heart} title="No retention activity recorded yet." compact />
                ) : (
                  <ul className="space-y-3">
                    {retentionTimeline.map((entry, i) => (
                      <li key={i} className="flex gap-3 text-sm">
                        <span className="mt-0.5 rounded-full border border-border-strong px-2 py-0.5 text-[10px] uppercase text-muted">
                          {entry.type}
                        </span>
                        <div>
                          <p>{entry.summary}</p>
                          <p className="text-xs text-muted-foreground">{new Date(entry.timestamp).toLocaleString()}</p>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="rounded-lg border border-border bg-surface p-6">
                <h2 className="mb-2 text-sm font-medium text-muted">AI summary</h2>
                {summary ? (
                  <p className="text-sm text-muted">{summary}</p>
                ) : (
                  <p className="text-sm text-muted">Not generated yet.</p>
                )}
                {summaryError && <p className="mt-2 text-xs text-danger">{summaryError}</p>}
                <button
                  onClick={loadSummary}
                  disabled={summaryLoading}
                  className="mt-4 w-full rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
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
