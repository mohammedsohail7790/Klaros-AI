"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import AppShell from "@/components/AppShell";
import { Badge } from "@/components/ui/Badge";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  addContentVariant,
  approveContent,
  getContent,
  publishContentVariant,
  rejectContent,
  requestContentApproval,
} from "@/lib/api";

const CHANNELS = ["Instagram", "LinkedIn", "TikTok", "Blog", "YouTube", "Facebook"];

interface ContentDetail {
  id: string;
  source_job_id: string | null;
  title: string;
  summary: string | null;
  status: string;
  ai_generated: boolean;
  variants: { id: string; channel: string; body_text: string | null; status: string }[];
}

export default function ContentDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { token, user, loading: authLoading } = useAuth();
  const [content, setContent] = useState<ContentDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [channel, setChannel] = useState(CHANNELS[0]);
  const [bodyText, setBodyText] = useState("");

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const result = await getContent(token, id);
      setContent(result);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load this content item.");
    } finally {
      setLoading(false);
    }
  }, [token, id]);

  useEffect(() => {
    load();
  }, [load]);

  async function runAction(fn: () => Promise<unknown>, successMessage = "Done.") {
    if (!token) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await fn();
      setNotice(successMessage);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Action failed.");
    } finally {
      setBusy(false);
    }
  }

  async function handleAddVariant(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    await runAction(() => addContentVariant(token, id, channel, bodyText.trim() || undefined));
    setBodyText("");
  }

  async function handlePublish(variantId: string) {
    if (!token) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await publishContentVariant(token, variantId);
      setNotice(
        "approval_request_id" in result
          ? "Publish requires approval — an approval request was created."
          : "Published."
      );
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Action failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <Link href="/marketing/content" className="text-sm text-muted hover:underline">
          ← Back to content
        </Link>

        {authLoading || loading ? (
          <p className="mt-4 text-sm text-muted">Loading content...</p>
        ) : error ? (
          <div className="mt-4 rounded-md border border-red-200 bg-red-50/30 p-4 text-sm text-red-700">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : !content ? (
          <p className="mt-4 text-sm text-muted">Content not found.</p>
        ) : (
          <div className="mt-4 space-y-6">
            <div className="rounded-lg border border-border bg-surface p-6">
              <div className="flex items-start justify-between">
                <div>
                  <h1 className="font-display text-2xl text-foreground">{content.title}</h1>
                  <p className="mt-1 text-sm text-muted">
                    {content.ai_generated ? "AI generated" : "Manually created"}
                    {content.source_job_id && " · from a completed job"}
                  </p>
                </div>
                <Badge status={content.status}>{content.status}</Badge>
              </div>
              {content.summary && <p className="mt-3 text-sm text-muted">{content.summary}</p>}

              {notice && (
                <div className="mt-4 rounded-md border border-emerald-200 bg-emerald-50/30 p-3 text-sm text-emerald-700">
                  {notice}
                </div>
              )}

              <div className="mt-4 flex gap-2">
                {(content.status === "IDEA" || content.status === "DRAFT") && (
                  <button
                    disabled={busy}
                    onClick={() => runAction(() => requestContentApproval(token!, id))}
                    className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
                  >
                    Request approval
                  </button>
                )}
                {content.status === "PENDING_APPROVAL" && (
                  <>
                    <button
                      disabled={busy}
                      onClick={() => runAction(() => approveContent(token!, id))}
                      className="rounded-md bg-surface px-3 py-1.5 text-sm font-medium text-emerald-700 hover:bg-surface-muted"
                    >
                      Approve
                    </button>
                    <button
                      disabled={busy}
                      onClick={() => runAction(() => rejectContent(token!, id))}
                      className="rounded-md border border-red-200 px-3 py-1.5 text-sm text-red-600 hover:bg-red-50/30"
                    >
                      Reject
                    </button>
                  </>
                )}
              </div>
            </div>

            <div className="rounded-lg border border-border bg-surface p-6">
              <h2 className="mb-3 text-sm font-medium text-muted">Channel variants</h2>
              {content.variants.length === 0 ? (
                <p className="text-sm text-muted">No variants yet — add one per channel below.</p>
              ) : (
                <ul className="space-y-3">
                  {content.variants.map((v) => (
                    <li key={v.id} className="rounded-md border border-border-strong bg-surface-muted p-3 text-sm">
                      <div className="flex items-center justify-between">
                        <div>
                          <span className="font-medium">{v.channel}</span>{" "}
                          <Badge status={v.status}>{v.status}</Badge>
                        </div>
                        {v.status !== "PUBLISHED" && content.status === "APPROVED" && (
                          <button
                            disabled={busy}
                            onClick={() => handlePublish(v.id)}
                            className="text-xs underline text-muted hover:text-foreground disabled:opacity-50"
                          >
                            Publish
                          </button>
                        )}
                      </div>
                      {v.body_text && <p className="mt-2 text-muted">{v.body_text}</p>}
                    </li>
                  ))}
                </ul>
              )}

              <form onSubmit={handleAddVariant} className="mt-4 space-y-2 border-t border-border pt-4">
                <div className="flex items-end gap-2">
                  <div>
                    <label className="block text-xs text-muted">Channel</label>
                    <select
                      value={channel}
                      onChange={(e) => setChannel(e.target.value)}
                      className="rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                    >
                      {CHANNELS.map((c) => (
                        <option key={c} value={c}>
                          {c}
                        </option>
                      ))}
                    </select>
                  </div>
                  <button
                    type="submit"
                    disabled={busy}
                    className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
                  >
                    Add variant
                  </button>
                </div>
                <textarea
                  value={bodyText}
                  onChange={(e) => setBodyText(e.target.value)}
                  placeholder="Variant copy (optional)"
                  rows={3}
                  className="w-full rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm"
                />
              </form>
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
