"use client";

/**
 * The builder workspace: chat on the left, a live sandboxed preview in the middle, and files / versions / checks / publishing beside it.
 * Every figure and status on this page comes from the backend; nothing is simulated.
 */

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { ApiError, BUILDER_ACTIVE_STATES, BuilderMessage, BuilderProject, BuilderRun, BuilderValidation, BuilderVersion, builderApi } from "@/lib/api";
import ChatPanel from "@/components/builder/ChatPanel";
import PreviewPane from "@/components/builder/PreviewPane";
import { ChecksPanel, FilesPanel, PublishPanel, VersionsPanel } from "@/components/builder/SidePanels";
import { cn } from "@/lib/cn";

const TABS = [
  { key: "files", label: "Files" },
  { key: "versions", label: "Versions" },
  { key: "checks", label: "Checks" },
  { key: "publish", label: "Publish" },
] as const;

export default function BuilderWorkspacePage() {
  return (
    <Suspense fallback={null}>
      <Workspace />
    </Suspense>
  );
}

function Workspace() {
  const { id } = useParams<{ id: string }>();
  const search = useSearchParams();
  const { token, user } = useAuth();
  const toast = useToast();
  const [project, setProject] = useState<BuilderProject | null>(null);
  const [run, setRun] = useState<BuilderRun | null>(null);
  const [messages, setMessages] = useState<BuilderMessage[]>([]);
  const [versions, setVersions] = useState<BuilderVersion[]>([]);
  const [files, setFiles] = useState<{ path: string; sha: string; size: number }[]>([]);
  const [validation, setValidation] = useState<BuilderValidation | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [tab, setTab] = useState<(typeof TABS)[number]["key"]>("files");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastVersion = useRef<string | null>(null);

  const refreshPreview = useCallback(async () => {
    if (!token) return;
    setPreviewLoading(true);
    try {
      setPreviewUrl((await builderApi.previewToken(token, id)).url);
      setPreviewError(null);
    } catch (e) {
      setPreviewUrl(null);
      setPreviewError(e instanceof ApiError && e.status !== 404 ? e.message : null);
    } finally {
      setPreviewLoading(false);
    }
  }, [token, id]);

  const loadAll = useCallback(async () => {
    if (!token) return;
    try {
      const { project: p, latest_run } = await builderApi.get(token, id);
      setProject(p);
      setRun((cur) => (cur && BUILDER_ACTIVE_STATES.includes(cur.state) && (!latest_run || latest_run.id !== cur.id) ? cur : latest_run));
      const [m, v] = await Promise.all([builderApi.messages(token, id), builderApi.versions(token, id)]);
      setMessages(m);
      setVersions(v);
      if (p.current_version_id) {
        const f = await builderApi.files(token, id);
        setFiles(f.files);
        setValidation(f.validation);
        if (lastVersion.current !== p.current_version_id) {
          lastVersion.current = p.current_version_id;
          await refreshPreview();
        }
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "We couldn't load this project.");
    }
  }, [token, id, refreshPreview]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  // While a run is active, ask the server where it is; when it finishes, reload everything once.
  const active = !!run && BUILDER_ACTIVE_STATES.includes(run.state);
  useEffect(() => {
    if (!token || !run || !active) return;
    const t = window.setInterval(async () => {
      try {
        const r = await builderApi.run(token, id, run.id);
        setRun(r);
        if (!BUILDER_ACTIVE_STATES.includes(r.state)) {
          if (r.state === "ready") toast.success("Preview updated.");
          else if (r.state === "failed") toast.danger("That didn't work — your last version is unchanged.");
          await loadAll();
        }
      } catch {
        /* a blip: the next tick tries again */
      }
    }, 1500);
    return () => window.clearInterval(t);
  }, [token, id, run, active, loadAll, toast]);

  // Arriving from the start screen with ?run=… attaches to that run.
  const attached = useRef(false);
  useEffect(() => {
    const rid = search.get("run");
    if (!token || !rid || attached.current) return;
    attached.current = true;
    builderApi.run(token, id, rid).then(setRun).catch(() => {});
  }, [token, id, search]);

  async function send(message: string) {
    if (!token) return;
    setSending(true);
    try {
      const { run: r } = await builderApi.send(token, id, message);
      setRun(r);
      setMessages((m) => [...m, { id: `local-${Date.now()}`, role: "user", content: message, run_id: r.id, created_at: new Date().toISOString() }]);
    } catch (e) {
      toast.danger(e instanceof ApiError ? e.message : "Couldn't send that.");
    } finally {
      setSending(false);
    }
  }

  async function cancel() {
    if (!token || !run) return;
    try {
      setRun(await builderApi.cancel(token, id, run.id));
    } catch (e) {
      toast.danger(e instanceof ApiError ? e.message : "Couldn't cancel.");
    }
  }

  if (error) {
    return (
      <AppShell user={user}>
        <div className="mx-auto max-w-3xl px-6 py-10"><Alert variant="danger">{error}</Alert><Link href="/builder" className="mt-4 inline-block text-sm text-accent-hover underline">Back to projects</Link></div>
      </AppShell>
    );
  }
  if (!project || !token) {
    return (
      <AppShell user={user}>
        <div className="mx-auto max-w-6xl px-5 py-10 sm:px-8 sm:py-12"><Skeleton rows={5} /></div>
      </AppShell>
    );
  }

  return (
    <AppShell user={user} crumbs={[{ label: "App Builder", href: "/builder" }, { label: project.name }]}>
      <div className="flex h-[calc(100vh-3.6rem)] min-h-[760px] flex-col">
        <div className="flex items-center gap-3 border-b border-border bg-surface px-4 py-2">
          <Link href="/builder" aria-label="Back to projects" className="rounded-xl p-1 text-muted hover:bg-surface-muted"><ArrowLeft className="h-4 w-4" /></Link>
          <h1 className="truncate text-sm font-semibold text-foreground">{project.name}</h1>
          <Badge status={project.status === "ready" ? "ACTIVE" : project.status === "failed" ? "FAILED" : "RUNNING"}>{project.status === "working" ? "Building" : project.status}</Badge>
          {project.published_version_id && <Badge status="PUBLISHED">Live</Badge>}
          <span className="ml-auto hidden text-xs text-muted sm:inline">{project.routes.length} routes{versions[0] ? ` · version ${versions[0].number}` : ""}</span>
        </div>
        {/* Wide screens: chat | preview | details. Narrower: chat | preview with the details underneath, so the preview is never squeezed into a phone-sized sliver. */}
        <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[20rem_minmax(0,1fr)] lg:grid-rows-[minmax(0,1fr)_minmax(0,15.5rem)] 2xl:grid-cols-[22rem_minmax(0,1fr)_28rem] 2xl:grid-rows-1">
          <div className="min-h-[24rem] border-b border-border bg-surface lg:row-span-2 lg:min-h-0 lg:border-b-0 lg:border-r 2xl:row-span-1"><ChatPanel messages={messages} run={run} onSend={send} onCancel={cancel} sending={sending} /></div>
          <div className="min-h-[30rem] lg:min-h-0"><PreviewPane previewUrl={previewUrl} routes={project.routes} loading={previewLoading} onRefresh={refreshPreview} building={active} error={previewError} /></div>
          <div className="flex min-h-[24rem] flex-col border-t border-border bg-surface lg:col-start-2 lg:min-h-0 2xl:col-start-3 2xl:border-l 2xl:border-t-0">
            <div role="tablist" aria-label="Project details" className="flex border-b border-border">
              {TABS.map((t) => (
                <button key={t.key} role="tab" aria-selected={tab === t.key} onClick={() => setTab(t.key)} className={cn("flex-1 border-b-2 px-3 py-2 text-sm font-medium transition-colors", tab === t.key ? "border-accent text-accent-hover" : "border-transparent text-muted hover:text-foreground")}>{t.label}</button>
              ))}
            </div>
            <div className="min-h-0 flex-1 overflow-hidden" role="tabpanel">
              {tab === "files" && <FilesPanel token={token} project={project} files={files} onSaved={loadAll} />}
              {tab === "versions" && <VersionsPanel token={token} project={project} versions={versions} onRestored={loadAll} />}
              {tab === "checks" && <ChecksPanel project={project} validation={validation} />}
              {tab === "publish" && <PublishPanel token={token} project={project} validation={validation} onChanged={loadAll} />}
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  );
}
