"use client";

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  KnowledgeFileRow,
  deleteKnowledgeFile,
  listKnowledgeFiles,
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
  const { token, user, loading: authLoading } = useAuth();
  const [files, setFiles] = useState<KnowledgeFileRow[] | null>(null);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [newPath, setNewPath] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

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
    setNotice(null);
  }

  async function handleSave() {
    if (!token || !selectedPath) return;
    setSaving(true);
    setError(null);
    try {
      await setKnowledgeFile(token, selectedPath, draft);
      setNotice("Saved.");
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

  const grouped: Record<string, KnowledgeFileRow[]> = {};
  for (const f of files ?? []) {
    const cat = categoryOf(f.path);
    (grouped[cat] ??= []).push(f);
  }
  const orderedCategories = [...CATEGORIES, ...Object.keys(grouped).filter((c) => !CATEGORIES.includes(c))];

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <h1 className="mb-2 text-xl font-semibold">Knowledge Layer</h1>
        <p className="mb-6 text-sm text-neutral-500">
          The real, editable source of truth for how your business actually operates — pricing rules,
          qualification criteria, brand voice. Klaros reads these where wired in (e.g. the Morning Brief's
          AI-mode prose follows <code className="text-neutral-400">brand/voice-guide.md</code> when a real
          AI provider is connected) — it never invents what should be here instead.
        </p>

        {notice && (
          <div className="mb-4 rounded-md border border-emerald-900 bg-emerald-950/30 p-3 text-sm text-emerald-300">
            {notice}
          </div>
        )}
        {error && (
          <div className="mb-4 rounded-md border border-red-900 bg-red-950/30 p-3 text-sm text-red-300">
            {error}
          </div>
        )}

        {authLoading || loading ? (
          <p className="text-sm text-neutral-500">Loading...</p>
        ) : (
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
            <div className="lg:col-span-1">
              <div className="mb-3 flex gap-2">
                <input
                  value={newPath}
                  onChange={(e) => setNewPath(e.target.value)}
                  placeholder="category/new-file.md"
                  className="flex-1 rounded-md border border-neutral-700 bg-neutral-900 px-2 py-1.5 text-sm"
                />
                <button
                  onClick={handleCreate}
                  disabled={saving || !newPath.trim()}
                  className="rounded-md border border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-900 disabled:opacity-50"
                >
                  New
                </button>
              </div>

              {orderedCategories.map((cat) =>
                grouped[cat] && grouped[cat].length > 0 ? (
                  <div key={cat} className="mb-4">
                    <h2 className="mb-1 text-xs font-medium uppercase text-neutral-500">{cat}</h2>
                    <div className="space-y-1">
                      {grouped[cat].map((f) => (
                        <div
                          key={f.path}
                          className={`flex items-center justify-between rounded-md border px-2 py-1.5 text-sm ${
                            selectedPath === f.path ? "border-neutral-400 bg-neutral-900" : "border-neutral-800"
                          }`}
                        >
                          <button onClick={() => selectFile(f)} className="flex-1 truncate text-left">
                            {titleOf(f.path)}
                          </button>
                          <button
                            onClick={() => handleDelete(f.path)}
                            className="ml-2 text-xs text-red-400 hover:text-white"
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
                <p className="text-sm text-neutral-500">No knowledge files yet.</p>
              )}
            </div>

            <div className="lg:col-span-2">
              {!selectedPath ? (
                <p className="text-sm text-neutral-500">Select a file to view or edit it.</p>
              ) : (
                <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-5">
                  <div className="mb-3 flex items-center justify-between">
                    <h2 className="font-medium">{selectedPath}</h2>
                    <button
                      onClick={handleSave}
                      disabled={saving}
                      className="rounded-md border border-emerald-800 bg-emerald-950/30 px-3 py-1.5 text-sm text-emerald-300 hover:bg-emerald-950/60 disabled:opacity-50"
                    >
                      Save
                    </button>
                  </div>
                  <textarea
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    rows={20}
                    className="w-full rounded-md border border-neutral-700 bg-black p-3 font-mono text-sm text-neutral-200"
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
