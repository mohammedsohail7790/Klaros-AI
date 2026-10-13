"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, Copy, Download, ExternalLink, FileCode2, History, Lock, Rocket, Undo2, XCircle } from "lucide-react";
import { ApiError, BuilderProject, BuilderValidation, BuilderVersion, builderApi } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Alert } from "@/components/ui/Alert";
import { useToast } from "@/components/ui/Toast";
import { cn } from "@/lib/cn";
import { CHECK_LABEL, FEATURE_STATUS, VERSION_KIND, timeAgo } from "./labels";

export function FilesPanel({ token, project, files, onSaved }: { token: string; project: BuilderProject; files: { path: string; size: number }[]; onSaved: () => void }) {
  const toast = useToast();
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [original, setOriginal] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const visible = useMemo(() => files.filter((f) => f.path.toLowerCase().includes(filter.toLowerCase())), [files, filter]);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    setError(null);
    builderApi.file(token, project.id, selected).then((f) => { if (!cancelled) { setContent(f.content); setOriginal(f.content); } }).catch((e) => !cancelled && setError(e instanceof ApiError ? e.message : "Couldn't open that file."));
    return () => { cancelled = true; };
  }, [selected, token, project.id, project.current_version_id]);

  async function save() {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      await builderApi.saveFile(token, project.id, selected, content);
      setOriginal(content);
      toast.success("Saved. Your edit is kept when the project is regenerated.");
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't save.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid h-full min-h-0 grid-cols-[13rem_1fr]">
      <div className="flex min-h-0 flex-col border-r border-border">
        <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Find a file" aria-label="Find a file" className="klaros-input m-2 !w-auto !py-1.5 text-xs" />
        <ul className="min-h-0 flex-1 overflow-y-auto pb-2 text-xs">
          {visible.map((f) => (
            <li key={f.path}>
              <button type="button" onClick={() => setSelected(f.path)} className={cn("flex w-full items-center gap-1.5 truncate px-3 py-1.5 text-left hover:bg-surface-muted", selected === f.path && "bg-accent-soft font-medium text-accent-hover")}>
                {project.locked_paths.includes(f.path) ? <Lock className="h-3 w-3 shrink-0" aria-label="Edited by hand" /> : <FileCode2 className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden="true" />}
                <span className="truncate">{f.path}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
      <div className="flex min-h-0 flex-col">
        {!selected ? (
          <p className="m-auto max-w-xs p-6 text-center text-sm text-muted">Pick a file to read it. You can edit any file; it must still pass the project checks, and hand-edited files are never overwritten by chat edits.</p>
        ) : (
          <>
            <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
              <p className="truncate font-mono text-xs text-foreground">{selected}</p>
              <Button size="sm" onClick={save} disabled={busy || content === original}>{busy ? "Checking…" : "Save"}</Button>
            </div>
            {error && <div className="p-2"><Alert variant="danger">{error}</Alert></div>}
            <label htmlFor="file-editor" className="sr-only">Contents of {selected}</label>
            <textarea id="file-editor" value={content} onChange={(e) => setContent(e.target.value)} spellCheck={false} className="min-h-0 flex-1 resize-none bg-surface p-3 font-mono text-xs leading-relaxed text-foreground outline-none" />
          </>
        )}
      </div>
    </div>
  );
}

export function VersionsPanel({ token, project, versions, onRestored }: { token: string; project: BuilderProject; versions: BuilderVersion[]; onRestored: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  async function restore(v: BuilderVersion) {
    if (!window.confirm(`Restore version ${v.number}? It becomes a new version; nothing is lost.`)) return;
    setBusy(v.id);
    try {
      await builderApi.restore(token, project.id, v.id);
      toast.success(`Restored version ${v.number}.`);
      onRestored();
    } catch (e) {
      toast.danger(e instanceof ApiError ? e.message : "Couldn't restore that version.");
    } finally {
      setBusy(null);
    }
  }
  return (
    <ul className="divide-y divide-border overflow-y-auto">
      {versions.map((v) => {
        const c = v.changes;
        return (
          <li key={v.id} className="p-4">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="flex flex-wrap items-center gap-2 text-sm font-semibold text-foreground">
                  Version {v.number}
                  <span className="rounded-md bg-surface-muted px-1.5 py-0.5 text-xs font-medium text-muted">{VERSION_KIND[v.kind]}</span>
                  {v.id === project.current_version_id && <span className="rounded-md bg-success/10 px-1.5 py-0.5 text-xs font-medium text-success">Current</span>}
                  {v.id === project.published_version_id && <span className="rounded-md bg-accent-soft px-1.5 py-0.5 text-xs font-medium text-accent-hover">Live</span>}
                </p>
                <p className="text-xs text-muted">{timeAgo(v.created_at)} · +{c.added?.length ?? 0} added · {c.changed?.length ?? 0} changed · {c.removed?.length ?? 0} removed</p>
              </div>
              {v.id !== project.current_version_id && (
                <Button variant="secondary" size="sm" disabled={busy === v.id} onClick={() => restore(v)}><Undo2 className="h-3.5 w-3.5" aria-hidden="true" />Restore</Button>
              )}
            </div>
            {v.summary && <p className="mt-2 line-clamp-3 text-xs leading-relaxed text-muted">{v.summary}</p>}
          </li>
        );
      })}
      {versions.length === 0 && <li className="p-6 text-sm text-muted">No versions yet.</li>}
    </ul>
  );
}

export function ChecksPanel({ project, validation }: { project: BuilderProject; validation: BuilderValidation | null }) {
  const issues = validation?.issues ?? [];
  return (
    <div className="space-y-6 overflow-y-auto p-4">
      <section>
        <h3 className="mb-2 text-sm font-semibold text-foreground">Checks on the current version</h3>
        {!validation ? <p className="text-sm text-muted">No checks yet.</p> : (
          <ul className="space-y-1.5">
            {Object.entries(validation.checks ?? {}).map(([k, ok]) => (
              <li key={k} className="flex items-start gap-2 text-sm">
                {ok ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" aria-label="Passed" /> : <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-danger" aria-label="Failed" />}
                <span className={ok ? "text-foreground" : "text-danger"}>{CHECK_LABEL[k] ?? k}</span>
              </li>
            ))}
          </ul>
        )}
        {issues.filter((i) => i.severity === "warning").length > 0 && (
          <ul className="mt-3 space-y-1.5">
            {issues.filter((i) => i.severity === "warning").slice(0, 8).map((i, n) => (
              <li key={n} className="flex items-start gap-2 text-xs text-muted"><AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" aria-hidden="true" />{i.path ? <span><span className="font-mono">{i.path}</span>: </span> : null}{i.message}</li>
            ))}
          </ul>
        )}
      </section>
      <section>
        <h3 className="mb-2 text-sm font-semibold text-foreground">What works, and what doesn&apos;t</h3>
        <ul className="space-y-2">
          {project.features.map((f) => {
            const s = FEATURE_STATUS[f.status];
            return (
              <li key={f.key} className="rounded-xl border border-border p-3">
                <div className="flex items-center justify-between gap-2"><p className="text-sm font-medium text-foreground">{f.label}</p><span className={cn("shrink-0 rounded-md px-2 py-0.5 text-xs font-medium", s.tone)}>{s.label}</span></div>
                <p className="mt-1 text-xs text-muted">{f.note || s.hint}</p>
              </li>
            );
          })}
          {project.features.length === 0 && <li className="text-sm text-muted">No feature list for this project.</li>}
        </ul>
        {project.assumptions.length > 0 && (
          <div className="mt-3"><Alert variant="info"><p className="mb-1 font-medium">Assumptions made</p><ul className="list-disc space-y-0.5 pl-4 text-xs">{project.assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul></Alert></div>
        )}
      </section>
    </div>
  );
}

export function PublishPanel({ token, project, validation, onChanged }: { token: string; project: BuilderProject; validation: BuilderValidation | null; onChanged: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [ack, setAck] = useState(false);
  const [url, setUrl] = useState<string | null>(null);
  const examples = validation?.stats?.example_sections ?? 0;
  const live = !!project.published_version_id;
  const livePublishedIsCurrent = project.published_version_id === project.current_version_id;

  async function publish() {
    setBusy(true);
    try {
      const r = await builderApi.publish(token, project.id, ack);
      setUrl(r.url);
      toast.success("Published. Your site is live.");
      onChanged();
    } catch (e) {
      toast.danger(e instanceof ApiError ? e.message : "Couldn't publish.");
    } finally {
      setBusy(false);
    }
  }
  async function unpublish() {
    setBusy(true);
    try {
      await builderApi.unpublish(token, project.id);
      setUrl(null);
      toast.success("Taken down.");
      onChanged();
    } catch (e) {
      toast.danger(e instanceof ApiError ? e.message : "Couldn't take it down.");
    } finally {
      setBusy(false);
    }
  }
  async function download() {
    try {
      await builderApi.downloadExport(token, project.id, `${project.slug}.zip`);
    } catch (e) {
      toast.danger(e instanceof ApiError ? e.message : "Couldn't export.");
    }
  }

  return (
    <div className="space-y-5 overflow-y-auto p-4">
      <section className="rounded-xl border border-border p-4">
        <p className="flex items-center gap-2 text-sm font-semibold text-foreground"><Rocket className="h-4 w-4 text-accent" aria-hidden="true" />{live ? "Your site is live" : "Not published yet"}</p>
        <p className="mt-1 text-xs text-muted">Publishing makes the current version public. Edits you make afterwards stay private until you publish again.</p>
        {live && !livePublishedIsCurrent && <p className="mt-2 text-xs text-warning">You have changes that aren&apos;t live yet.</p>}
        {examples > 0 && !livePublishedIsCurrent && (
          <label className="mt-3 flex items-start gap-2 rounded-xl bg-warning/10 p-3 text-xs text-foreground">
            <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} className="mt-0.5" />
            <span>{examples} section(s) still contain example content (sample records, pricing, text). I&apos;ve reviewed it and want to publish anyway.</span>
          </label>
        )}
        <div className="mt-3 flex flex-wrap gap-2">
          <Button onClick={publish} disabled={busy || !project.current_version_id || livePublishedIsCurrent || (examples > 0 && !ack)}>{busy ? "Working…" : live ? "Publish latest" : "Publish"}</Button>
          {live && <Button variant="secondary" onClick={unpublish} disabled={busy}>Take it down</Button>}
        </div>
        {url && (
          <p className="mt-3 flex items-center gap-2 break-all text-xs"><a href={url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 font-medium text-accent-hover underline">{url}<ExternalLink className="h-3 w-3" aria-hidden="true" /></a>
            <button type="button" aria-label="Copy link" onClick={() => navigator.clipboard?.writeText(url)} className="rounded p-1 text-muted hover:bg-surface-muted"><Copy className="h-3.5 w-3.5" /></button></p>
        )}
        <p className="mt-3 text-xs leading-relaxed text-muted-foreground">Published projects are served from Klaros. A custom domain isn&apos;t set up automatically — download the files to host them anywhere.</p>
      </section>
      <section className="rounded-xl border border-border p-4">
        <p className="flex items-center gap-2 text-sm font-semibold text-foreground"><Download className="h-4 w-4 text-accent" aria-hidden="true" />Download</p>
        <p className="mt-1 text-xs text-muted">Get every file as a .zip you can host on any static host.</p>
        <Button className="mt-3" variant="secondary" onClick={download} disabled={!project.current_version_id}>Download .zip</Button>
      </section>
    </div>
  );
}

export const TAB_ICONS = { files: FileCode2, versions: History };
