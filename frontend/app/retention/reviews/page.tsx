"use client";

import { useCallback, useEffect, useState } from "react";
import { Star, ThumbsUp } from "lucide-react";
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
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
export default function ReviewsPage() {
  const toast = useToast();
  const { token, user, loading: authLoading } = useAuth();
  const [requests, setRequests] = useState<ReviewRequestRow[]>([]);
  const [feedback, setFeedback] = useState<FeedbackRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
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
    try {
      const result = await sendReviewRequest(token, id);
      if (result && "status" in result && (result as any).status === "pending_approval") {
        toast.success("Sending a review request requires approval — an ApprovalRequest has been created.");
      } else {
        toast.success("Review request sent.");
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
    try {
      await recordReviewConsent(token, feedbackId, consent);
      toast.success(consent ? "Consent recorded — this review can now become marketing content." : "Recorded: customer declined.");
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
    try {
      const result = await createContentFromReview(token, feedbackId);
      if (result && "status" in result && (result as { status?: string }).status === "pending_approval") {
        toast.success("Creating marketing content requires approval — an ApprovalRequest has been created.");
      } else {
        toast.success("Marketing content idea created — review it on the Content page.");
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
    try {
      const result = await recordFeedback(token, {
        customer_id: fbCustomerId.trim(),
        rating: Number(fbRating),
        comment: fbComment.trim() || undefined,
      });
      toast.success(`Feedback recorded (sentiment: ${result.sentiment ?? "n/a"}).`);
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
      <div className="mx-auto w-full max-w-6xl px-5 py-8 sm:px-8 sm:py-12">
        <h1 className="font-display text-[2rem] font-semibold leading-[1.05] tracking-[-0.03em] text-foreground sm:text-[2.75rem] mb-3">Reviews &amp; Reputation</h1>
        <p className="mb-6 text-xs text-muted">
          External Google/Yelp reviews are NOT CONNECTED — no external review has ever been fetched. Everything below is
          internal.
        </p>

        {authLoading || loading ? (
          <Skeleton />
        ) : error ? (
          <div className="rounded-xl border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">Retry</button>
          </div>
        ) : (
          <>
            {negativeFeedback.length > 0 && (
              <div className="mb-6 rounded-xl border border-danger/25 bg-danger/[0.06] p-4">
                <h2 className="mb-2 text-sm font-medium text-danger">Service recovery opportunities ({negativeFeedback.length})</h2>
                <ul className="space-y-1 text-sm">
                  {negativeFeedback.map((f) => (
                    <li key={f.id} className="text-danger">
                      Rating {f.rating}/5{f.comment ? `: "${f.comment}"` : ""} — {new Date(f.received_at).toLocaleDateString()}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <h2 className="mb-3 font-display text-[1.35rem] font-semibold tracking-[-0.02em] text-foreground">Record feedback</h2>
            <div className="mb-6 flex flex-wrap items-end gap-2">
              <div>
                <label className="block text-xs text-muted">Customer ID</label>
                <input
                  value={fbCustomerId}
                  onChange={(e) => setFbCustomerId(e.target.value)}
                  className="w-64 rounded-xl border border-border-strong bg-surface-muted px-2 py-1 text-sm"
                />
              </div>
              <div>
                <label className="block text-xs text-muted">Rating (1-5)</label>
                <select
                  value={fbRating}
                  onChange={(e) => setFbRating(e.target.value)}
                  className="rounded-xl border border-border-strong bg-surface-muted px-2 py-1 text-sm"
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
                  className="w-64 rounded-xl border border-border-strong bg-surface-muted px-2 py-1 text-sm"
                />
              </div>
              <button
                disabled={busy || !fbCustomerId.trim()}
                onClick={handleRecordFeedback}
                className="klaros-btn-secondary !px-3 !py-1.5 disabled:opacity-50"
              >
                Record
              </button>
            </div>

            <h2 className="mb-3 font-display text-[1.35rem] font-semibold tracking-[-0.02em] text-foreground">Review requests</h2>
            {requests.length === 0 ? (
              <div className="mb-6">
                <EmptyState icon={Star} title="No review requests yet." />
              </div>
            ) : (
              <div className="mb-6 klaros-table-wrap">
                <table className="klaros-table">
                  <thead>
                    <tr>
                      <th className="px-5 py-4">Status</th>
                      <th className="px-5 py-4">Channel</th>
                      <th className="px-5 py-4">Requested at</th>
                      <th className="px-5 py-4"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {requests.map((r) => (
                      <tr key={r.id} className="border-t border-border">
                        <td className="px-5 py-4">
                          <Badge status={r.status}>{r.status}</Badge>
                        </td>
                        <td className="px-5 py-4 text-muted">{r.channel}</td>
                        <td className="px-5 py-4 text-muted">{r.requested_at ? new Date(r.requested_at).toLocaleString() : "—"}</td>
                        <td className="px-5 py-4">
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

            <h2 className="mb-3 font-display text-[1.35rem] font-semibold tracking-[-0.02em] text-foreground">
              Positive feedback ({positiveFeedback.length})
            </h2>
            {positiveFeedback.length === 0 ? (
              <EmptyState icon={ThumbsUp} title="No positive feedback yet." />
            ) : (
              <ul className="space-y-2 text-sm text-muted">
                {positiveFeedback.map((f) => (
                  <li key={f.id} className="rounded-2xl bg-surface-muted p-3">
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
                            className="underline text-success hover:text-foreground"
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
                          <span className="text-success">Consent recorded.</span>
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
