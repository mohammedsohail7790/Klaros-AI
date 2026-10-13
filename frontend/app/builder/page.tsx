"use client";

/**
 * App Builder: describe a website or application in plain words; Klaros plans the pages, writes the project, checks it and shows a live
 * preview. The number of pages comes from the request, not from a template.
 */

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowRight, FileCode2, Globe, Loader2, Sparkles, Trash2 } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { Button } from "@/components/ui/Button";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { ApiError, BuilderProject, builderApi } from "@/lib/api";
import { EXAMPLE_PROMPTS, timeAgo } from "@/components/builder/labels";

export default function BuilderHome() {
  const { token, user } = useAuth();
  const router = useRouter();
  const toast = useToast();
  const [projects, setProjects] = useState<BuilderProject[] | null>(null);
  const [prompt, setPrompt] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      setProjects(await builderApi.list(token));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "We couldn't load your projects.");
      setProjects([]);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function start(e: React.FormEvent) {
    e.preventDefault();
    if (!token || busy) return;
    setBusy(true);
    setError(null);
    try {
      const { project, run } = await builderApi.create(token, prompt.trim(), name.trim() || undefined);
      router.push(`/builder/${project.id}?run=${run.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "We couldn't start that. Please try again.");
      setBusy(false);
    }
  }

  async function remove(p: BuilderProject) {
    if (!token || !window.confirm(`Delete “${p.name}” and all its versions? This can't be undone.`)) return;
    try {
      await builderApi.remove(token, p.id);
      toast.success("Project deleted.");
      load();
    } catch (err) {
      toast.danger(err instanceof ApiError ? err.message : "Couldn't delete it.");
    }
  }

  return (
    <AppShell user={user}>
      <div className="mx-auto max-w-6xl px-4 py-8 sm:px-8">
        <div className="relative mb-10 overflow-hidden rounded-2xl border border-border bg-surface p-6 shadow-card sm:p-9">
          <div aria-hidden="true" className="absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-accent via-violet-500 to-fuchsia-500" />
          <div className="max-w-3xl">
            <span className="inline-flex items-center gap-1.5 rounded-full bg-accent-soft px-3 py-1 text-xs font-semibold text-accent-hover">
              <Sparkles className="h-3.5 w-3.5" aria-hidden="true" /> App Builder
            </span>
            <h1 className="mt-4 text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">What do you want to build?</h1>
            <p className="mt-2 text-muted">
              Describe a website in your own words. Klaros works out the pages it needs, writes the project, checks it, and shows you a live preview you can keep refining by chat.
            </p>
          </div>
          <form onSubmit={start} className="mt-6 max-w-3xl space-y-3">
            <label htmlFor="builder-prompt" className="sr-only">Describe your project</label>
            <textarea
              id="builder-prompt"
              required
              minLength={10}
              maxLength={6000}
              rows={4}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="e.g. A real-estate marketplace with property search, filters, detail pages, saved properties and enquiry forms…"
              className="klaros-input resize-y text-base"
            />
            <div className="flex flex-wrap items-center gap-3">
              <label htmlFor="builder-name" className="sr-only">Business or project name (optional)</label>
              <input id="builder-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} placeholder="Name (optional)" className="klaros-input max-w-[16rem]" />
              <Button type="submit" size="lg" disabled={busy || prompt.trim().length < 10}>
                {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Sparkles className="h-4 w-4" aria-hidden="true" />}
                {busy ? "Starting…" : "Build it"}
              </Button>
            </div>
            <div className="flex flex-wrap gap-2 pt-1" aria-label="Examples">
              {EXAMPLE_PROMPTS.map((p) => (
                <button key={p} type="button" onClick={() => setPrompt(p)} className="rounded-full border border-border bg-surface px-3 py-1 text-left text-xs text-muted transition-colors hover:border-border-strong hover:text-foreground">
                  {p.length > 64 ? p.slice(0, 62) + "…" : p}
                </button>
              ))}
            </div>
          </form>
          {error && <div className="mt-4 max-w-3xl"><Alert variant="danger">{error}</Alert></div>}
          <p className="mt-5 max-w-3xl text-xs text-muted-foreground">
            Projects are static websites (HTML, CSS and JavaScript). Things that need a server — sign-in, payments, a database — are listed as not possible instead of being faked.
          </p>
        </div>

        <h2 className="mb-3 text-lg font-semibold tracking-tight text-foreground">Your projects</h2>
        {projects === null ? (
          <Skeleton stats={0} rows={3} />
        ) : projects.length === 0 ? (
          <p className="klaros-card border-dashed p-8 text-center text-sm text-muted">No projects yet. Describe one above to get started.</p>
        ) : (
          <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {projects.map((p) => (
              <li key={p.id} className="klaros-card klaros-card-interactive flex flex-col p-5">
                <div className="flex items-start justify-between gap-2">
                  <Link href={`/builder/${p.id}`} className="min-w-0 flex-1">
                    <h3 className="truncate text-base font-semibold text-foreground">{p.name}</h3>
                    <p className="mt-0.5 text-xs text-muted">Updated {timeAgo(p.updated_at)}</p>
                  </Link>
                  <Badge status={p.status === "ready" ? "ACTIVE" : p.status === "failed" ? "FAILED" : p.status === "working" ? "RUNNING" : "DRAFT"}>{p.status === "working" ? "Building" : p.status}</Badge>
                </div>
                <dl className="mt-4 flex gap-5 text-sm">
                  <div><dt className="flex items-center gap-1 text-xs text-muted"><FileCode2 className="h-3 w-3" aria-hidden="true" />Routes</dt><dd className="font-semibold text-foreground">{p.routes.length}</dd></div>
                  <div><dt className="flex items-center gap-1 text-xs text-muted"><Globe className="h-3 w-3" aria-hidden="true" />Live</dt><dd className="font-semibold text-foreground">{p.published_version_id ? "Yes" : "No"}</dd></div>
                </dl>
                <div className="mt-4 flex items-center justify-between border-t border-border pt-3">
                  <Link href={`/builder/${p.id}`} className="inline-flex items-center gap-1 text-sm font-medium text-accent-hover hover:underline">Open <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" /></Link>
                  <button type="button" onClick={() => remove(p)} aria-label={`Delete ${p.name}`} className="rounded-md p-1.5 text-muted-foreground hover:bg-danger/10 hover:text-danger"><Trash2 className="h-4 w-4" /></button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </AppShell>
  );
}
