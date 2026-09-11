"use client";

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  FeedbackRow,
  ReviewRequestRow,
  createContentFromReview,
  listFeedback,
  listReviewRequests,
  recordFeedback,
  recordReviewConsent,
  sendReviewRequest,
} from "@/lib/api";

import { Badge } from "@/components/ui/Badge";
export default function ReviewsPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [requests, setRequests] = useState<ReviewRequestRow[]>([]);
  const [feedback, setFeedback] = useState<FeedbackRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [fbCustomerId, setFbCustomerId] = useState("");
  const [fbRating, setFbRating] = useState("5");
  const [fbComment, setFbComment] = useState("");

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [requestsResult, feedbackResult] = await Promise.all([listReviewRequests(token), listFeedback(token)]);
      setRequests(requestsResult.review_requests);
      setFeedback(feedbackResult.feedback);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load reviews.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleSend(id: string) {
    if (!token) return;
    setBusy(true);
    setNotice(null);
    try {
      const result = await sendReviewRequest(token, id);
      if (result && "status" in result && (result as any).status === "pending_approval") {
        setNotice("Sending a review request requires approval — an ApprovalRequest has been created.");
      } else {
        setNotice("Review request sent.");
      }
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to send review request.");
    } finally {
      setBusy(false);
    }
  }

  async function handleConsent(feedbackId: string, consent: boolean) {
    if (!token) return;
    setBusy(true);
    setNotice(null);
    try {
      await recordReviewConsent(token, feedbackId, consent);
      setNotice(consent ? "Consent recorded — this review can now become marketing content." : "Recorded: customer declined.");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to record consent.");
    } finally {
      setBusy(false);
    }
  }

  async function handleCreateContent(feedbackId: string) {
    if (!token) return;
    setBusy(true);
    setNotice(null);
    try {
      const result = await createContentFromReview(token, feedbackId);
      if (result && "status" in result && (result as { status?: string }).status === "pending_approval") {
        setNotice("Creating marketing content requires approval — an ApprovalRequest has been created.");
      } else {
        setNotice("Marketing content idea created — review it on the Content page.");
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to create content from this review.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRecordFeedback() {
    if (!token || !fbCustomerId.trim()) return;
    setBusy(true);
    setNotice(null);
    try {
      const result = await recordFeedback(token, {
        customer_id: fbCustomerId.trim(),
        rating: Number(fbRating),
        comment: fbComment.trim() || undefined,
      });
      setNotice(`Feedback recorded (sentiment: ${result.sentiment ?? "n/a"}).`);
      setFbComment("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to record feedback.");
    } finally {
      setBusy(false);
    }
  }

  const negativeFeedback = feedback.filter((f) => f.sentiment === "NEGATIVE");
  const positiveFeedback = feedback.filter((f) => f.sentiment === "POSITIVE");

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <h1 className="font-display text-2xl text-foreground mb-6">Reviews &amp; Reputation</h1>
        <p className="mb-6 text-xs text-muted">
          External Google/Yelp reviews are NOT CONNECTED — no external review has ever been fetched. Everything below is
          internal.
        </p>

        {notice && <div className="mb-4 rounded-md border border-emerald-200 bg-emerald-50/30 p-3 text-sm text-emerald-700">{notice}</div>}

        {authLoading || loading ? (
          <p className="text-sm text-muted">Loading...</p>
        ) : error ? (
          <div className="rounded-md border border-red-200 bg-red-50/30 p-4 text-sm text-red-700">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">Retry</button>
          </div>
        ) : (
          <>
            {negativeFeedback.length > 0 && (
              <div className="mb-6 rounded-md border border-red-200 bg-red-50/30 p-4">
                <h2 className="mb-2 text-sm font-medium text-red-700">Service recovery opportunities ({negativeFeedback.length})</h2>
                <ul className="space-y-1 text-sm">
                  {negativeFeedback.map((f) => (
                    <li key={f.id} className="text-red-700">
                      Rating {f.rating}/5{f.comment ? `: "${f.comment}"` : ""} — {new Date(f.received_at).toLocaleDateString()}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <h2 className="mb-3 text-sm font-medium text-muted">Record feedback</h2>
            <div className="mb-6 flex flex-wrap items-end gap-2">
              <div>
                <label className="block text-xs text-muted">Customer ID</label>
                <input
                  value={fbCustomerId}
                  onChange={(e) => setFbCustomerId(e.target.value)}
                  className="w-64 rounded-md border border-border-strong bg-surface-muted px-2 py-1 text-sm"
                />
              </div>
              <div>
                <label className="block text-xs text-muted">Rating (1-5)</label>
                <select
                  value={fbRating}
                  onChange={(e) => setFbRating(e.target.value)}
                  className="rounded-md border border-border-strong bg-surface-muted px-2 py-1 text-sm"
                >
                  {[1, 2, 3, 4, 5].map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs text-muted">Comment (optional)</label>
                <input
                  value={fbComment}
                  onChange={(e) => setFbComment(e.target.value)}
                  className="w-64 rounded-md border border-border-strong bg-surface-muted px-2 py-1 text-sm"
                />
              </div>
              <button
                disabled={busy || !fbCustomerId.trim()}
                onClick={handleRecordFeedback}
                className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
              >
                Record
              </button>
            </div>

            <h2 className="mb-3 text-sm font-medium text-muted">Review requests</h2>
            {requests.length === 0 ? (
              <p className="mb-6 text-sm text-muted">No review requests yet.</p>
            ) : (
              <div className="mb-6 klaros-table-wrap">
                <table className="klaros-table">
                  <thead className="bg-surface text-muted">
                    <tr>
                      <th className="px-4 py-2">Status</th>
                      <th className="px-4 py-2">Channel</th>
                      <th className="px-4 py-2">Requested at</th>
                      <th className="px-4 py-2"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {requests.map((r) => (
                      <tr key={r.id} className="border-t border-border">
                        <td className="px-4 py-2">
                          <Badge status={r.status}>{r.status}</Badge>
                        </td>
                        <td className="px-4 py-2 text-muted">{r.channel}</td>
                        <td className="px-4 py-2 text-muted">{r.requested_at ? new Date(r.requested_at).toLocaleString() : "—"}</td>
                        <td className="px-4 py-2">
                          {r.status === "ELIGIBLE" && (
                            <button disabled={busy} onClick={() => handleSend(r.id)} className="text-xs underline text-muted hover:text-foreground">
                              Send
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <h2 className="mb-3 text-sm font-medium text-muted">
              Positive feedback ({positiveFeedback.length})
            </h2>
            {positiveFeedback.length === 0 ? (
              <p className="text-sm text-muted">No positive feedback yet.</p>
            ) : (
              <ul className="space-y-2 text-sm text-muted">
                {positiveFeedback.map((f) => (
                  <li key={f.id} className="rounded-md border border-border bg-surface p-3">
                    <div>
                      Rating {f.rating}/5{f.comment ? `: "${f.comment}"` : ""}
                    </div>
                    <div className="mt-2 flex flex-wrap items-center gap-3 text-xs">
                      {f.consent_to_use_publicly === null && (
                        <>
                          <span className="text-muted">Use publicly as marketing content?</span>
                          <button
                            disabled={busy}
                            onClick={() => handleConsent(f.id, true)}
                            className="underline text-emerald-600 hover:text-foreground"
                          >
                            Customer said yes
                          </button>
                          <button
                            disabled={busy}
                            onClick={() => handleConsent(f.id, false)}
                            className="underline text-muted hover:text-foreground"
                          >
                            Customer declined
                          </button>
                        </>
                      )}
                      {f.consent_to_use_publicly === false && (
                        <span className="text-muted-foreground">Customer declined to have this used publicly.</span>
                      )}
                      {f.consent_to_use_publicly === true && (
                        <>
                          <span className="text-emerald-600">Consent recorded.</span>
                          <button
                            disabled={busy}
                            onClick={() => handleCreateContent(f.id)}
                            className="underline text-muted hover:text-foreground"
                          >
                            Create marketing content from this review
                          </button>
                        </>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </AppShell>
  );
}
