"use client";

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
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

const VIEWS = ["ALL", "IDEA", "DRAFT", "PENDING_APPROVAL", "APPROVED", "SCHEDULED", "PUBLISHED"];

export default function ContentPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [status, setStatus] = useState("ALL");
  const [items, setItems] = useState<MarketingContentItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
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
    setNotice(null);
    try {
      await fn();
      setNotice("Done.");
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
        <h1 className="mb-6 text-xl font-semibold">Content Engine</h1>

        <form onSubmit={handleCreateIdea} className="mb-6 flex items-end gap-2">
          <div>
            <label className="block text-xs text-neutral-500">New content idea</label>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Before/after HVAC repair in Dallas"
              className="w-80 rounded-md border border-neutral-700 bg-neutral-900 px-2 py-1.5 text-sm"
            />
          </div>
          <button type="submit" disabled={busy || !title.trim()} className="rounded-md border border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-900 disabled:opacity-50">
            Add idea
          </button>
        </form>

        <div className="mb-4 flex flex-wrap gap-2">
          {VIEWS.map((v) => (
            <button
              key={v}
              onClick={() => setStatus(v)}
              className={`rounded-full border px-3 py-1 text-xs ${status === v ? "border-white bg-white text-black" : "border-neutral-700 text-neutral-400"}`}
            >
              {v}
            </button>
          ))}
        </div>

        {notice && <div className="mb-4 rounded-md border border-emerald-900 bg-emerald-950/30 p-3 text-sm text-emerald-300">{notice}</div>}

        {authLoading || loading ? (
          <p className="text-sm text-neutral-500">Loading...</p>
        ) : error ? (
          <div className="rounded-md border border-red-900 bg-red-950/30 p-4 text-sm text-red-300">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">Retry</button>
          </div>
        ) : items.length === 0 ? (
          <p className="text-sm text-neutral-500">No content items.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-neutral-800">
            <table className="w-full text-left text-sm">
              <thead className="bg-neutral-950 text-neutral-500">
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
                  <tr key={c.id} className="border-t border-neutral-900">
                    <td className="px-4 py-2">{c.title}</td>
                    <td className="max-w-md truncate px-4 py-2 text-neutral-400">{c.summary}</td>
                    <td className="px-4 py-2">
                      <span className="rounded-full border border-neutral-700 px-2 py-0.5 text-xs">{c.status}</span>
                    </td>
                    <td className="px-4 py-2 text-neutral-500">{c.ai_generated ? "Yes" : "No"}</td>
                    <td className="px-4 py-2 space-x-2">
                      {(c.status === "IDEA" || c.status === "DRAFT") && (
                        <button
                          disabled={busy}
                          onClick={() => runAction(() => requestContentApproval(token!, c.id))}
                          className="text-xs underline text-neutral-400 hover:text-white"
                        >
                          Request approval
                        </button>
                      )}
                      {c.status === "PENDING_APPROVAL" && (
                        <>
                          <button disabled={busy} onClick={() => runAction(() => approveContent(token!, c.id))} className="text-xs underline text-emerald-400 hover:text-white">
                            Approve
                          </button>
                          <button disabled={busy} onClick={() => runAction(() => rejectContent(token!, c.id))} className="text-xs underline text-red-400 hover:text-white">
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
