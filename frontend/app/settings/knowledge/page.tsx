"use client";

import { useCallback, useEffect, useState } from "react";
import { BookOpen, Search } from "lucide-react";
import AppShell from "@/components/AppShell";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { useAuth } from "@/lib/useAuth";
import { useToast } from "@/components/ui/Toast";
import {
  ApiError,
  KnowledgeAskResponse,
  KnowledgeFileRow,
  KnowledgeSearchResultRow,
  askKnowledge,
  deleteKnowledgeFile,
  indexKnowledgeFile,
  listKnowledgeFiles,
  searchKnowledge,
  setKnowledgeFile,
} from "@/lib/api";

const CATEGORIES = ["office", "market", "customer", "delivery", "finance", "compliance", "brand"];

function categoryOf(path: string): string {
  return path.split("/")[0] ?? "other";
}

function titleOf(path: string): string {
  const name = path.split("/").pop() ?? path;
  return name.replace(/\.md$/, "").replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export default function KnowledgePage() {
  const toast = useToast();
  const { token, user, loading: authLoading } = useAuth();
  const [files, setFiles] = useState<KnowledgeFileRow[] | null>(null);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [newPath, setNewPath] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [mode, setMode] = useState<"browse" | "search">("browse");
  const [queryDraft, setQueryDraft] = useState("");
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [searchResults, setSearchResults] = useState<KnowledgeSearchResultRow[] | null>(null);
  const [askResult, setAskResult] = useState<KnowledgeAskResponse | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const result = await listKnowledgeFiles(token);
      setFiles(result.files);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load knowledge files.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  function selectFile(f: KnowledgeFileRow) {
    setSelectedPath(f.path);
    setDraft(f.content);
  }

  async function handleSave() {
    if (!token || !selectedPath) return;
    setSaving(true);
    setError(null);
    try {
      await setKnowledgeFile(token, selectedPath, draft);
      // Saving a file only writes its content — it doesn't become
      // searchable (Test retrieval / Ask AI) until it's re-indexed. Do
      // that here so a save always leaves the file actually reachable,
      // never silently stale. Best-effort: an indexing hiccup (e.g. no
      // embedding provider configured) shouldn't block the save itself.
      try {
        const indexResult = await indexKnowledgeFile(token, selectedPath);
        toast.success(
          indexResult.status === "skipped_no_provider"
            ? "Saved. Not indexed for search — no AI provider configured."
            : "Saved and indexed for search."
        );
      } catch {
        toast.success("Saved, but re-indexing for search failed — try again from the file list.");
      }
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to save.");
    } finally {
      setSaving(false);
    }
  }

  async function handleCreate() {
    if (!token || !newPath.trim()) return;
    const path = newPath.trim().replace(/^\/+/, "");
    setSaving(true);
    setError(null);
    try {
      await setKnowledgeFile(token, path, "");
      setNewPath("");
      await load();
      setSelectedPath(path);
      setDraft("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to create file.");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(path: string) {
    if (!token) return;
    setSaving(true);
    try {
      await deleteKnowledgeFile(token, path);
      if (selectedPath === path) {
        setSelectedPath(null);
        setDraft("");
      }
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to delete.");
    } finally {
      setSaving(false);
    }
  }

  async function handleSearch() {
    if (!token || !queryDraft.trim()) return;
    setSearching(true);
    setSearchError(null);
    setSearchResults(null);
    setAskResult(null);
    try {
      const result = await searchKnowledge(token, queryDraft.trim());
      if (!result.available) {
        setSearchError(result.error_detail ?? "Search is unavailable.");
      } else {
        setSearchResults(result.results);
      }
    } catch (err) {
      setSearchError(err instanceof ApiError ? err.message : "Unable to search.");
    } finally {
      setSearching(false);
    }
  }

  async function handleAsk() {
    if (!token || !queryDraft.trim()) return;
    setSearching(true);
    setSearchError(null);
    setSearchResults(null);
    setAskResult(null);
    try {
      const result = await askKnowledge(token, queryDraft.trim());
      if (!result.available) {
        setSearchError(result.error_detail ?? "AI answering is unavailable.");
      } else {
        setAskResult(result);
      }
    } catch (err) {
      setSearchError(err instanceof ApiError ? err.message : "Unable to get an answer.");
    } finally {
      setSearching(false);
    }
  }

  const grouped: Record<string, KnowledgeFileRow[]> = {};
  for (const f of files ?? []) {
    const cat = categoryOf(f.path);
    (grouped[cat] ??= []).push(f);
  }
  const orderedCategories = [...CATEGORIES, ...Object.keys(grouped).filter((c) => !CATEGORIES.includes(c))];

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <h1 className="font-display text-2xl text-foreground mb-2">Knowledge Layer</h1>
        <p className="mb-6 text-sm text-muted">
          The real, editable source of truth for how your business actually operates — pricing rules,
          qualification criteria, brand voice. Klaros reads these where wired in (e.g. the Morning Brief's
          AI-mode prose follows <code className="text-muted">brand/voice-guide.md</code> when a real
          AI provider is connected) — it never invents what should be here instead.
        </p>

        <div className="mb-6 flex gap-2 border-b border-border">
          <button
            onClick={() => setMode("browse")}
            className={`px-3 py-2 text-sm ${mode === "browse" ? "border-b-2 border-border text-foreground" : "text-muted"}`}
          >
            Browse &amp; edit
          </button>
          <button
            onClick={() => setMode("search")}
            className={`px-3 py-2 text-sm ${mode === "search" ? "border-b-2 border-border text-foreground" : "text-muted"}`}
          >
            Test retrieval
          </button>
        </div>

        {error && (
          <div className="mb-4 rounded-md border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">
            {error}
          </div>
        )}

        {mode === "search" ? (
          <div className="max-w-3xl">
            <p className="mb-4 text-sm text-muted">
              Real, tenant-scoped semantic search over the files on the left — chunked, embedded, and ranked by
              similarity. &quot;Ask&quot; additionally sends the top matching excerpts to a real AI provider,
              bounded to only what was actually retrieved, with source citations; if no AI provider is
              configured it says so honestly rather than fabricating an answer.
            </p>
            <div className="mb-4 flex gap-2">
              <input
                value={queryDraft}
                onChange={(e) => setQueryDraft(e.target.value)}
                placeholder="e.g. what is our plumbing hourly rate?"
                className="flex-1 rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm"
                onKeyDown={(e) => e.key === "Enter" && handleSearch()}
              />
              <button
                onClick={handleSearch}
                disabled={searching || !queryDraft.trim()}
                className="rounded-md border border-border-strong px-3 py-2 text-sm hover:bg-surface-muted disabled:opacity-50"
              >
                Search
              </button>
              <button
                onClick={handleAsk}
                disabled={searching || !queryDraft.trim()}
                className="rounded-md border border-success/20 bg-success/[0.06] px-3 py-2 text-sm text-success hover:bg-success/10 disabled:opacity-50"
              >
                Ask AI
              </button>
            </div>

            {searching && <p className="text-sm text-muted">Working...</p>}
            {searchError && (
              <div className="mb-4 rounded-md border border-warning/25 bg-warning/[0.07] p-3 text-sm text-warning">
                {searchError}
              </div>
            )}

            {askResult && (
              <div className="mb-4 rounded-lg border border-border bg-surface p-4">
                <p className="mb-2 text-sm text-foreground">{askResult.answer}</p>
                {askResult.answered_from_excerpts && askResult.sources && askResult.sources.length > 0 && (
                  <p className="text-xs text-muted">Sources: {askResult.sources.join(", ")}</p>
                )}
              </div>
            )}

            {searchResults && (
              <div className="space-y-3">
                {searchResults.length === 0 && (
                  <EmptyState icon={Search} title="No matching knowledge found." compact />
                )}
                {searchResults.map((r) => (
                  <div key={`${r.file_path}-${r.chunk_index}`} className="rounded-lg border border-border bg-surface p-4">
                    <div className="mb-1 flex items-center justify-between text-xs text-muted">
                      <span>{r.file_path}</span>
                      <span>score {r.score.toFixed(3)}</span>
                    </div>
                    <p className="text-sm text-muted">{r.content}</p>
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : authLoading || loading ? (
          <Skeleton />
        ) : (
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
            <div className="lg:col-span-1">
              <div className="mb-3 flex gap-2">
                <input
                  value={newPath}
                  onChange={(e) => setNewPath(e.target.value)}
                  placeholder="category/new-file.md"
                  className="flex-1 rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                />
                <button
                  onClick={handleCreate}
                  disabled={saving || !newPath.trim()}
                  className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
                >
                  New
                </button>
              </div>

              {orderedCategories.map((cat) =>
                grouped[cat] && grouped[cat].length > 0 ? (
                  <div key={cat} className="mb-4">
                    <h2 className="mb-1 text-xs font-medium uppercase text-muted">{cat}</h2>
                    <div className="space-y-1">
                      {grouped[cat].map((f) => (
                        <div
                          key={f.path}
                          className={`flex items-center justify-between rounded-md border px-2 py-1.5 text-sm ${
                            selectedPath === f.path ? "border-border-strong bg-surface-muted" : "border-border"
                          }`}
                        >
                          <button onClick={() => selectFile(f)} className="flex-1 truncate text-left">
                            {titleOf(f.path)}
                          </button>
                          <button
                            onClick={() => handleDelete(f.path)}
                            className="ml-2 text-xs text-danger hover:text-foreground"
                          >
                            delete
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null
              )}
              {(!files || files.length === 0) && (
                <EmptyState icon={BookOpen} title="No knowledge files yet." compact />
              )}
            </div>

            <div className="lg:col-span-2">
              {!selectedPath ? (
                <p className="text-sm text-muted">Select a file to view or edit it.</p>
              ) : (
                <div className="rounded-lg border border-border bg-surface p-5">
                  <div className="mb-3 flex items-center justify-between">
                    <h2 className="font-medium">{selectedPath}</h2>
                    <button
                      onClick={handleSave}
                      disabled={saving}
                      className="rounded-md border border-success/20 bg-success/[0.06] px-3 py-1.5 text-sm text-success hover:bg-success/10 disabled:opacity-50"
                    >
                      Save
                    </button>
                  </div>
                  <textarea
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    rows={20}
                    className="w-full rounded-md border border-border-strong bg-background p-3 font-mono text-sm text-foreground"
                  />
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
