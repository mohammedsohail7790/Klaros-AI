"use client";

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, SEOPageSummary, generateSEOPage, listSEOPages, publishSEOPage } from "@/lib/api";

export default function SEOPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [pages, setPages] = useState<SEOPageSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [service, setService] = useState("");
  const [location, setLocation] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      setPages((await listSEOPages(token)).pages);
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

  async function handlePublish(pageId: string) {
    if (!token) return;
    setBusy(true);
    setNotice(null);
    try {
      const result = await publishSEOPage(token, pageId);
      if (result && "status" in result && (result as any).status === "pending_approval") {
        setNotice("Publishing requires approval — an ApprovalRequest has been created.");
      } else {
        setNotice("Page published.");
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
        <h1 className="mb-6 text-xl font-semibold">Local SEO Pages</h1>

        <form onSubmit={handleGenerate} className="mb-6 flex flex-wrap items-end gap-2">
          <div>
            <label className="block text-xs text-neutral-500">Service</label>
            <input value={service} onChange={(e) => setService(e.target.value)} placeholder="HVAC Repair" className="rounded-md border border-neutral-700 bg-neutral-900 px-2 py-1.5 text-sm" />
          </div>
          <div>
            <label className="block text-xs text-neutral-500">Location</label>
            <input value={location} onChange={(e) => setLocation(e.target.value)} placeholder="Dallas, TX" className="rounded-md border border-neutral-700 bg-neutral-900 px-2 py-1.5 text-sm" />
          </div>
          <button type="submit" disabled={busy || !service.trim() || !location.trim()} className="rounded-md border border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-900 disabled:opacity-50">
            Generate draft
          </button>
        </form>

        {notice && <div className="mb-4 rounded-md border border-emerald-900 bg-emerald-950/30 p-3 text-sm text-emerald-300">{notice}</div>}

        {authLoading || loading ? (
          <p className="text-sm text-neutral-500">Loading...</p>
        ) : error ? (
          <div className="rounded-md border border-red-900 bg-red-950/30 p-4 text-sm text-red-300">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">Retry</button>
          </div>
        ) : pages.length === 0 ? (
          <p className="text-sm text-neutral-500">No SEO pages yet.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-neutral-800">
            <table className="w-full text-left text-sm">
              <thead className="bg-neutral-950 text-neutral-500">
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
                  <tr key={p.id} className="border-t border-neutral-900">
                    <td className="px-4 py-2">{p.service}</td>
                    <td className="px-4 py-2 text-neutral-400">{p.location}</td>
                    <td className="px-4 py-2 text-neutral-400">{p.title}</td>
                    <td className="px-4 py-2">
                      <span className="rounded-full border border-neutral-700 px-2 py-0.5 text-xs">{p.status}</span>
                      {p.ai_generated && <span className="ml-2 text-xs text-neutral-500">AI GENERATED</span>}
                    </td>
                    <td className="px-4 py-2">
                      {p.status === "DRAFT" && (
                        <button disabled={busy} onClick={() => handlePublish(p.id)} className="text-xs underline text-neutral-400 hover:text-white">
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
      </div>
    </AppShell>
  );
}
