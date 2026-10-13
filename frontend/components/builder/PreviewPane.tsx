"use client";

import { useMemo, useState } from "react";
import { ExternalLink, Laptop, Loader2, RefreshCw, Smartphone, Tablet } from "lucide-react";
import type { BuilderRoute } from "@/lib/api";
import { cn } from "@/lib/cn";
import { routeDir } from "./labels";

const VIEWPORTS = [
  { key: "desktop", label: "Desktop", width: "100%", icon: Laptop },
  { key: "tablet", label: "Tablet", width: "768px", icon: Tablet },
  { key: "mobile", label: "Mobile", width: "390px", icon: Smartphone },
] as const;

export default function PreviewPane({ previewUrl, routes, loading, onRefresh, building, error }: { previewUrl: string | null; routes: BuilderRoute[]; loading: boolean; onRefresh: () => void; building: boolean; error: string | null }) {
  const [vp, setVp] = useState<(typeof VIEWPORTS)[number]["key"]>("desktop");
  const [path, setPath] = useState("/");
  const [nonce, setNonce] = useState(0);
  const pages = useMemo(() => routes.filter((r) => !r.detail), [routes]);
  const width = VIEWPORTS.find((v) => v.key === vp)!.width;
  const src = previewUrl ? `${previewUrl}${routeDir(path)}` : null;

  return (
    <section aria-label="Preview" className="flex h-full min-h-0 flex-col bg-surface-muted/50">
      <div className="flex flex-wrap items-center gap-2 border-b border-border bg-surface px-3 py-2">
        <label htmlFor="preview-route" className="sr-only">Page</label>
        <select id="preview-route" value={path} onChange={(e) => setPath(e.target.value)} className="klaros-input !w-auto max-w-[14rem] !py-1.5 text-sm">
          {pages.map((r) => <option key={r.path} value={r.path}>{r.title} — {r.path}</option>)}
        </select>
        <div role="group" aria-label="Screen size" className="ml-1 flex rounded-xl border border-border p-0.5">
          {VIEWPORTS.map((v) => (
            <button key={v.key} type="button" aria-pressed={vp === v.key} aria-label={v.label} title={v.label} onClick={() => setVp(v.key)} className={cn("rounded-xl p-1.5 transition-colors", vp === v.key ? "bg-accent-soft text-accent-hover" : "text-muted hover:text-foreground")}>
              <v.icon className="h-4 w-4" />
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-1">
          <button type="button" onClick={() => { setNonce((n) => n + 1); onRefresh(); }} aria-label="Refresh preview" title="Refresh" className="rounded-xl p-1.5 text-muted hover:bg-surface-muted hover:text-foreground"><RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} /></button>
          {src && <a href={src} target="_blank" rel="noopener noreferrer" aria-label="Open preview in a new tab" title="Open in a new tab" className="rounded-xl p-1.5 text-muted hover:bg-surface-muted hover:text-foreground"><ExternalLink className="h-4 w-4" /></a>}
        </div>
      </div>
      <div className="relative min-h-0 flex-1 overflow-auto p-3">
        {building && <div className="absolute inset-x-0 top-0 z-10 flex items-center justify-center gap-2 bg-accent px-3 py-1.5 text-xs font-medium text-accent-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> Updating — you are seeing the last working version</div>}
        {error ? (
          <p className="m-auto mt-16 max-w-sm text-center text-sm text-danger">{error}</p>
        ) : src ? (
          <div className="mx-auto h-full overflow-hidden rounded-xl border border-border bg-white shadow-raised transition-[width] duration-200" style={{ width, maxWidth: "100%" }}>
            {/* No allow-same-origin: the page runs as an opaque origin and can never touch Klaros' own session or storage. */}
            <iframe key={`${src}-${nonce}`} title="Project preview" src={`${src}?t=${nonce}`} sandbox="allow-scripts allow-forms" referrerPolicy="no-referrer" className="h-full min-h-[480px] w-full border-0" />
          </div>
        ) : (
          <p className="m-auto mt-16 text-center text-sm text-muted">{building ? "Your first version is being built…" : "No preview yet."}</p>
        )}
      </div>
    </section>
  );
}
