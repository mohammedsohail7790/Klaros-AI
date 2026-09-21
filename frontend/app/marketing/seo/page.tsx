"use client";

import { useCallback, useEffect, useState } from "react";
import { TrendingUp } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  LocalListingRow,
  LocalReviewRow,
  SEOKeywordRow,
  SEOPageSummary,
  createLocalListing,
  createSEOOpportunity,
  generateSEOPage,
  listLocalListings,
  listLocalReviews,
  listSEOKeywords,
  listSEOPages,
  publishSEOPage,
  recordLocalReview,
  recordSEOKeyword,
  respondToLocalReview,
} from "@/lib/api";

import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";

const PRIORITIES = ["LOW", "MEDIUM", "HIGH"];

export default function SEOPage() {
  const toast = useToast();
  const { token, user, loading: authLoading } = useAuth();
  const [pages, setPages] = useState<SEOPageSummary[]>([]);
  const [keywords, setKeywords] = useState<SEOKeywordRow[]>([]);
  const [listings, setListings] = useState<LocalListingRow[]>([]);
  const [reviews, setReviews] = useState<LocalReviewRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [service, setService] = useState("");
  const [location, setLocation] = useState("");
  const [busy, setBusy] = useState(false);

  const [keyword, setKeyword] = useState("");
  const [keywordLocation, setKeywordLocation] = useState("");
  const [oppService, setOppService] = useState("");
  const [oppLocation, setOppLocation] = useState("");
  const [oppRationale, setOppRationale] = useState("");
  const [oppPriority, setOppPriority] = useState("MEDIUM");

  const [listingName, setListingName] = useState("");
  const [listingCity, setListingCity] = useState("");
  const [listingState, setListingState] = useState("");

  const [reviewListingId, setReviewListingId] = useState("");
  const [reviewRating, setReviewRating] = useState("5");
  const [reviewAuthor, setReviewAuthor] = useState("");
  const [reviewBody, setReviewBody] = useState("");
  const [respondingReviewId, setRespondingReviewId] = useState<string | null>(null);
  const [responseText, setResponseText] = useState("");

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [pagesResult, keywordsResult, listingsResult, reviewsResult] = await Promise.all([
        listSEOPages(token),
        listSEOKeywords(token),
        listLocalListings(token),
        listLocalReviews(token),
      ]);
      setPages(pagesResult.pages);
      setKeywords(keywordsResult.keywords);
      setListings(listingsResult.listings);
      setReviews(reviewsResult.reviews);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load SEO pages.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleGenerate(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !service.trim() || !location.trim()) return;
    setBusy(true);
    try {
      await generateSEOPage(token, service.trim(), location.trim());
      setService("");
      setLocation("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to generate page.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRecordKeyword(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !keyword.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await recordSEOKeyword(token, { keyword: keyword.trim(), target_location: keywordLocation.trim() || undefined });
      setKeyword("");
      setKeywordLocation("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to record keyword.");
    } finally {
      setBusy(false);
    }
  }

  async function handleCreateOpportunity(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !oppService.trim() || !oppLocation.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await createSEOOpportunity(token, {
        service: oppService.trim(),
        location: oppLocation.trim(),
        rationale: oppRationale.trim() || undefined,
        priority: oppPriority,
      });
      setOppService("");
      setOppLocation("");
      setOppRationale("");
      toast.success("Opportunity recorded.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to record opportunity.");
    } finally {
      setBusy(false);
    }
  }

  async function handleCreateListing(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !listingName.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await createLocalListing(token, {
        business_name: listingName.trim(),
        city: listingCity.trim() || undefined,
        state: listingState.trim() || undefined,
      });
      setListingName("");
      setListingCity("");
      setListingState("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to create listing.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRecordReview(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !reviewListingId) return;
    setBusy(true);
    setError(null);
    try {
      await recordLocalReview(token, {
        listing_id: reviewListingId,
        rating: Number(reviewRating),
        author: reviewAuthor.trim() || undefined,
        body: reviewBody.trim() || undefined,
      });
      setReviewAuthor("");
      setReviewBody("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to record review.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRespondToReview(reviewId: string) {
    if (!token || !responseText.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const result = await respondToLocalReview(token, reviewId, responseText.trim());
      if (result && "status" in result && (result as any).status === "pending_approval") {
        toast.success("Responding requires approval — an ApprovalRequest has been created.");
      } else {
        toast.success("Response posted.");
      }
      setRespondingReviewId(null);
      setResponseText("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to respond to review.");
    } finally {
      setBusy(false);
    }
  }

  async function handlePublish(pageId: string) {
    if (!token) return;
    setBusy(true);
    try {
      const result = await publishSEOPage(token, pageId);
      if (result && "status" in result && (result as any).status === "pending_approval") {
        toast.success("Publishing requires approval — an ApprovalRequest has been created.");
      } else {
        toast.success("Page published.");
      }
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to publish page.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <h1 className="font-display text-2xl text-foreground mb-6">Local SEO Pages</h1>

        <form onSubmit={handleGenerate} className="mb-6 flex flex-wrap items-end gap-2">
          <div>
            <label className="block text-xs text-muted">Service</label>
            <input value={service} onChange={(e) => setService(e.target.value)} placeholder="HVAC Repair" className="rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm" />
          </div>
          <div>
            <label className="block text-xs text-muted">Location</label>
            <input value={location} onChange={(e) => setLocation(e.target.value)} placeholder="Dallas, TX" className="rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm" />
          </div>
          <button type="submit" disabled={busy || !service.trim() || !location.trim()} className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50">
            Generate draft
          </button>
        </form>

        {authLoading || loading ? (
          <Skeleton />
        ) : error ? (
          <div className="rounded-md border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">Retry</button>
          </div>
        ) : pages.length === 0 ? (
          <EmptyState icon={TrendingUp} title="No SEO pages yet — generate a draft above to get started." />
        ) : (
          <div className="klaros-table-wrap">
            <table className="klaros-table">
              <thead className="bg-surface text-muted">
                <tr>
                  <th className="px-4 py-2">Service</th>
                  <th className="px-4 py-2">Location</th>
                  <th className="px-4 py-2">Title</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {pages.map((p) => (
                  <tr key={p.id} className="border-t border-border">
                    <td className="px-4 py-2">{p.service}</td>
                    <td className="px-4 py-2 text-muted">{p.location}</td>
                    <td className="px-4 py-2 text-muted">{p.title}</td>
                    <td className="px-4 py-2">
                      <Badge status={p.status}>{p.status}</Badge>
                      {p.ai_generated && <span className="ml-2 text-xs text-muted">AI GENERATED</span>}
                    </td>
                    <td className="px-4 py-2">
                      {p.status === "DRAFT" && (
                        <button disabled={busy} onClick={() => handlePublish(p.id)} className="text-xs underline text-muted hover:text-foreground">
                          Publish
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="mt-10 grid grid-cols-1 gap-6 lg:grid-cols-2">
          <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
            <h2 className="mb-3 text-sm font-medium text-muted">Keywords ({keywords.length})</h2>
            <form onSubmit={handleRecordKeyword} className="mb-3 flex flex-wrap gap-2">
              <input
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                placeholder="Keyword"
                className="flex-1 rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
              />
              <input
                value={keywordLocation}
                onChange={(e) => setKeywordLocation(e.target.value)}
                placeholder="Location (optional)"
                className="flex-1 rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
              />
              <button
                type="submit"
                disabled={busy || !keyword.trim()}
                className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
              >
                Record
              </button>
            </form>
            {keywords.length === 0 ? (
              <p className="text-xs text-muted">No keywords tracked yet.</p>
            ) : (
              <ul className="space-y-1 text-sm">
                {keywords.map((k) => (
                  <li key={k.id} className="flex items-center justify-between text-xs">
                    <span>
                      {k.keyword}
                      {k.target_location && ` — ${k.target_location}`}
                    </span>
                    <span className="text-muted">
                      {k.current_ranking != null ? `rank #${k.current_ranking}` : "no ranking yet"}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
            <h2 className="mb-3 text-sm font-medium text-muted">SEO opportunities</h2>
            <form onSubmit={handleCreateOpportunity} className="space-y-2">
              <div className="flex gap-2">
                <input
                  value={oppService}
                  onChange={(e) => setOppService(e.target.value)}
                  placeholder="Service"
                  className="flex-1 rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                />
                <input
                  value={oppLocation}
                  onChange={(e) => setOppLocation(e.target.value)}
                  placeholder="Location"
                  className="flex-1 rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                />
              </div>
              <input
                value={oppRationale}
                onChange={(e) => setOppRationale(e.target.value)}
                placeholder="Rationale (optional)"
                className="w-full rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
              />
              <div className="flex items-end gap-2">
                <select
                  value={oppPriority}
                  onChange={(e) => setOppPriority(e.target.value)}
                  className="rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                >
                  {PRIORITIES.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
                <button
                  type="submit"
                  disabled={busy || !oppService.trim() || !oppLocation.trim()}
                  className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
                >
                  Record opportunity
                </button>
              </div>
            </form>
          </div>
        </div>

        <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-2">
          <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
            <h2 className="mb-3 text-sm font-medium text-muted">Local listings ({listings.length})</h2>
            <form onSubmit={handleCreateListing} className="mb-3 space-y-2">
              <input
                value={listingName}
                onChange={(e) => setListingName(e.target.value)}
                placeholder="Business name"
                className="w-full rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
              />
              <div className="flex gap-2">
                <input
                  value={listingCity}
                  onChange={(e) => setListingCity(e.target.value)}
                  placeholder="City"
                  className="flex-1 rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                />
                <input
                  value={listingState}
                  onChange={(e) => setListingState(e.target.value)}
                  placeholder="State"
                  className="flex-1 rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                />
                <button
                  type="submit"
                  disabled={busy || !listingName.trim()}
                  className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
                >
                  Add
                </button>
              </div>
            </form>
            {listings.length === 0 ? (
              <p className="text-xs text-muted">No local listings yet.</p>
            ) : (
              <ul className="space-y-1 text-xs text-muted">
                {listings.map((l) => (
                  <li key={l.id}>
                    {l.business_name}
                    {l.city && ` — ${l.city}${l.state ? `, ${l.state}` : ""}`}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
            <h2 className="mb-3 text-sm font-medium text-muted">Local reviews ({reviews.length})</h2>
            <form onSubmit={handleRecordReview} className="mb-3 space-y-2">
              <select
                value={reviewListingId}
                onChange={(e) => setReviewListingId(e.target.value)}
                className="w-full rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
              >
                <option value="">Select a listing...</option>
                {listings.map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.business_name}
                  </option>
                ))}
              </select>
              <div className="flex gap-2">
                <select
                  value={reviewRating}
                  onChange={(e) => setReviewRating(e.target.value)}
                  className="rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                >
                  {[5, 4, 3, 2, 1].map((r) => (
                    <option key={r} value={r}>
                      {r} star
                    </option>
                  ))}
                </select>
                <input
                  value={reviewAuthor}
                  onChange={(e) => setReviewAuthor(e.target.value)}
                  placeholder="Author (optional)"
                  className="flex-1 rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                />
              </div>
              <textarea
                value={reviewBody}
                onChange={(e) => setReviewBody(e.target.value)}
                placeholder="Review text (optional)"
                rows={2}
                className="w-full rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
              />
              <button
                type="submit"
                disabled={busy || !reviewListingId}
                className="w-full rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
              >
                Record review
              </button>
            </form>
            {reviews.length === 0 ? (
              <p className="text-xs text-muted">No reviews recorded yet.</p>
            ) : (
              <ul className="space-y-2 text-xs">
                {reviews.map((r) => (
                  <li key={r.id} className="rounded-md border border-border-strong bg-surface-muted p-2">
                    <div className="flex items-center justify-between">
                      <span>
                        {r.rating}★ {r.author && `— ${r.author}`}
                      </span>
                      {r.responded && <span className="text-success">responded</span>}
                    </div>
                    {r.body && <p className="mt-1 text-muted">{r.body}</p>}
                    {!r.responded && (
                      respondingReviewId === r.id ? (
                        <div className="mt-2 flex items-center gap-2">
                          <input
                            value={responseText}
                            onChange={(e) => setResponseText(e.target.value)}
                            placeholder="Response"
                            className="flex-1 rounded-md border border-border-strong bg-surface px-2 py-1 text-xs"
                          />
                          <button
                            onClick={() => handleRespondToReview(r.id)}
                            disabled={busy || !responseText.trim()}
                            className="rounded-md border border-border-strong px-2 py-1 text-xs hover:bg-surface disabled:opacity-50"
                          >
                            Send
                          </button>
                        </div>
                      ) : (
                        <button
                          onClick={() => {
                            setRespondingReviewId(r.id);
                            setResponseText("");
                          }}
                          className="mt-1 text-xs underline text-muted hover:text-foreground"
                        >
                          Respond
                        </button>
                      )
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>
    </AppShell>
  );
}
