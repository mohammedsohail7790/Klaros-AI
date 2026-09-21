"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { FileText } from "lucide-react";
import AppShell from "@/components/AppShell";
import { EmptyState } from "@/components/ui/EmptyState";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  MarketingContentItem,
  approveContent,
  createContentIdea,
  listContent,
  rejectContent,
  requestContentApproval,
} from "@/lib/api";

import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
const VIEWS = ["ALL", "IDEA", "DRAFT", "PENDING_APPROVAL", "APPROVED", "SCHEDULED", "PUBLISHED"];

export default function ContentPage() {
  const toast = useToast();
  const { token, user, loading: authLoading } = useAuth();
  const [status, setStatus] = useState("ALL");
  const [items, setItems] = useState<MarketingContentItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      setItems((await listContent(token, status === "ALL" ? undefined : status)).content);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load content.");
    } finally {
      setLoading(false);
    }
  }, [token, status]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleCreateIdea(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !title.trim()) return;
    setBusy(true);
    try {
      await createContentIdea(token, title.trim());
      setTitle("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to create idea.");
    } finally {
      setBusy(false);
    }
  }

  async function runAction(fn: () => Promise<unknown>) {
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      await fn();
      toast.success("Done.");
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
        <h1 className="font-display text-2xl text-foreground mb-6">Content Engine</h1>

        <form onSubmit={handleCreateIdea} className="mb-6 flex items-end gap-2">
          <div>
            <label className="block text-xs text-muted">New content idea</label>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Before/after HVAC repair in Dallas"
              className="w-80 rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
            />
          </div>
          <button type="submit" disabled={busy || !title.trim()} className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50">
            Add idea
          </button>
        </form>

        <div className="mb-4 flex flex-wrap gap-2">
          {VIEWS.map((v) => (
            <button
              key={v}
              onClick={() => setStatus(v)}
              className={`rounded-full border px-3 py-1 text-xs ${status === v ? "border-foreground bg-surface text-foreground" : "border-border-strong text-muted"}`}
            >
              {v}
            </button>
          ))}
        </div>

        {authLoading || loading ? (
          <Skeleton />
        ) : error ? (
          <div className="rounded-md border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">Retry</button>
          </div>
        ) : items.length === 0 ? (
          <EmptyState icon={FileText} title="No content items." />
        ) : (
          <div className="klaros-table-wrap">
            <table className="klaros-table">
              <thead className="bg-surface text-muted">
                <tr>
                  <th className="px-4 py-2">Title</th>
                  <th className="px-4 py-2">Summary</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2">AI generated</th>
                  <th className="px-4 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {items.map((c) => (
                  <tr key={c.id} className="border-t border-border">
                    <td className="px-4 py-2">
                      <Link href={`/marketing/content/${c.id}`} className="underline hover:text-foreground">
                        {c.title}
                      </Link>
                    </td>
                    <td className="max-w-md truncate px-4 py-2 text-muted">{c.summary}</td>
                    <td className="px-4 py-2">
                      <Badge status={c.status}>{c.status}</Badge>
                    </td>
                    <td className="px-4 py-2 text-muted">{c.ai_generated ? "Yes" : "No"}</td>
                    <td className="px-4 py-2 space-x-2">
                      {(c.status === "IDEA" || c.status === "DRAFT") && (
                        <button
                          disabled={busy}
                          onClick={() => runAction(() => requestContentApproval(token!, c.id))}
                          className="text-xs underline text-muted hover:text-foreground"
                        >
                          Request approval
                        </button>
                      )}
                      {c.status === "PENDING_APPROVAL" && (
                        <>
                          <button disabled={busy} onClick={() => runAction(() => approveContent(token!, c.id))} className="text-xs underline text-success hover:text-foreground">
                            Approve
                          </button>
                          <button disabled={busy} onClick={() => runAction(() => rejectContent(token!, c.id))} className="text-xs underline text-danger hover:text-foreground">
                            Reject
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </AppShell>
  );
}
