"use client";

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, SEOPageSummary, generateSEOPage, listSEOPages, publishSEOPage } from "@/lib/api";

import { Badge } from "@/components/ui/Badge";
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

        {notice && <div className="mb-4 rounded-md border border-emerald-200 bg-emerald-50/30 p-3 text-sm text-emerald-700">{notice}</div>}

        {authLoading || loading ? (
          <p className="text-sm text-muted">Loading...</p>
        ) : error ? (
          <div className="rounded-md border border-red-200 bg-red-50/30 p-4 text-sm text-red-700">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">Retry</button>
          </div>
        ) : pages.length === 0 ? (
          <p className="text-sm text-muted">No SEO pages yet.</p>
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
      </div>
    </AppShell>
  );
}
