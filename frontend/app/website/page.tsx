"use client";

/**
 * Phase 12 §5: the minimum real Website Builder dashboard — website
 * overview, draft version view, page/section editing, theme editing,
 * preview, and publish/unpublish, all against the existing authenticated
 * backend API (backend/app/api/v1/websites.py). This page contains no
 * business logic of its own: every write is a direct call into that API,
 * which re-validates against the closed WebsiteSpecification schema
 * server-side — this file's own state is just a working copy for the UI.
 *
 * RBAC (Phase 13): buttons are not hidden by role — the backend is the
 * only authority (READ_WEBSITE/MANAGE_WEBSITE/PUBLISH_WEBSITE), and a
 * user lacking permission simply gets a 403 surfaced as an error banner,
 * exactly like every other authenticated page in this app.
 */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { Button } from "@/components/ui/Button";
import { Input, Label } from "@/components/ui/Input";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import {
  ApiError,
  RenderedWebsite,
  Website,
  WebsiteSection,
  WebsiteVersion,
  addWebsitePage,
  generateWebsite,
  getMyWebsite,
  listWebsiteVersions,
  newWebsiteDraft,
  previewWebsiteVersion,
  publishWebsiteVersion,
  replaceWebsitePageSections,
  unpublishWebsite,
  updateWebsiteTheme,
} from "@/lib/api";
import { COMPONENT_TYPES, SectionPropsForm, defaultDataSourceFor, defaultPropsFor } from "@/components/website/SectionEditor";

export default function WebsitePage() {
  const { token, user } = useAuth();
  const [website, setWebsite] = useState<Website | null | undefined>(undefined);
  const [versions, setVersions] = useState<WebsiteVersion[]>([]);
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setError(null);
    try {
      const w = await getMyWebsite(token);
      setWebsite(w);
      if (w) {
        const v = await listWebsiteVersions(token, w.id);
        setVersions(v);
        if (!selectedVersionId && v.length > 0) setSelectedVersionId(v[0].id);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load your website.");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleGenerate() {
    if (!token || busy) return;
    setBusy(true);
    setError(null);
    try {
      await generateWebsite(token);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to generate a website. Do you have an active Business Blueprint?");
    } finally {
      setBusy(false);
    }
  }

  if (website === undefined) {
    return (
      <AppShell user={user}>
        <div className="px-8 py-8">
          <Skeleton />
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <header className="mb-6 flex items-center justify-between">
          <h1 className="font-display text-2xl text-foreground">Website</h1>
          {website && (
            <a href={`/w/${user?.tenant_id}`} target="_blank" rel="noreferrer" className="text-sm text-accent underline">
              View public site
            </a>
          )}
        </header>

        {error && (
          <div className="mb-4 rounded-md border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">{error}</div>
        )}

        {!website ? (
          <EmptyState
            title="No website yet — generate a draft from your active Business Blueprint to get started."
            action={
              <Button onClick={handleGenerate} disabled={busy}>
                {busy ? "Generating..." : "Generate website"}
              </Button>
            }
          />
        ) : (
          <div className="grid grid-cols-1 gap-6 md:grid-cols-[280px_1fr]">
            <div>
              <div className="mb-3 text-sm text-muted">
                {website.name} <span className="text-muted-foreground">/{website.slug}</span>
              </div>
              <div className="space-y-2">
                {versions.map((v) => (
                  <button
                    key={v.id}
                    onClick={() => setSelectedVersionId(v.id)}
                    className={`w-full rounded-md border p-2 text-left text-sm ${
                      v.id === selectedVersionId ? "border-accent bg-accent-soft" : "border-border"
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span>v{v.version}</span>
                      <Badge status={v.status}>{v.status}</Badge>
                    </div>
                  </button>
                ))}
              </div>
              <Button variant="secondary" size="sm" className="mt-3 w-full" onClick={handleGenerate} disabled={busy}>
                Regenerate from blueprint
              </Button>
            </div>

            <div>
              {selectedVersionId && token && website && (
                <VersionEditor
                  key={selectedVersionId}
                  token={token}
                  website={website}
                  version={versions.find((v) => v.id === selectedVersionId)!}
                  onChanged={load}
                />
              )}
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}

function VersionEditor({
  token,
  website,
  version,
  onChanged,
}: {
  token: string;
  website: Website;
  version: WebsiteVersion;
  onChanged: () => void;
}) {
  const [rendered, setRendered] = useState<RenderedWebsite | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [activePageSlug, setActivePageSlug] = useState<string | null>(null);
  const [editSections, setEditSections] = useState<WebsiteSection[] | null>(null);
  const isDraft = version.status === "DRAFT";

  const loadPreview = useCallback(async () => {
    setError(null);
    try {
      const r = await previewWebsiteVersion(token, website.id, version.id);
      setRendered(r);
      if (!activePageSlug && r.pages.length > 0) setActivePageSlug(r.pages[0].slug);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load this version.");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, website.id, version.id]);

  useEffect(() => {
    loadPreview();
  }, [loadPreview]);

  const activePage = rendered?.pages.find((p) => p.slug === activePageSlug) ?? null;

  useEffect(() => {
    if (!activePage) {
      setEditSections(null);
      return;
    }
    // Reconstruct an editable WebsiteSection[] from the rendered tree. The
    // authenticated preview endpoint now echoes each section's saved
    // `data_source` back (backend/app/api/v1/websites.py's preview_version
    // overlay — editor-only, never present on the public render path), so
    // an existing PROVIDER_DIRECTORY/PROCEDURE_LIST section's provider key
    // rehydrates correctly after save/reload instead of starting blank.
    // `data_source` is `undefined` rather than `null` only if an older
    // cached response is somehow replayed; fall back to the generic
    // (always-null) default in that case rather than guessing a value.
    setEditSections(
      activePage.sections.map((s) => ({
        component_type: s.component_type as WebsiteSection["component_type"],
        props: s.props,
        data_source:
          s.data_source !== undefined
            ? s.data_source
            : defaultDataSourceFor(s.component_type as WebsiteSection["component_type"]),
      }))
    );
  }, [activePage]);

  async function saveSections() {
    if (!activePageSlug || !editSections || busy) return;
    setBusy(true);
    setError(null);
    try {
      await replaceWebsitePageSections(token, website.id, version.id, activePageSlug, editSections);
      await loadPreview();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to save sections.");
    } finally {
      setBusy(false);
    }
  }

  async function handleAddPage() {
    const slug = window.prompt("New page slug (lowercase-with-dashes):");
    if (!slug) return;
    const title = window.prompt("Page title:") || slug;
    setBusy(true);
    setError(null);
    try {
      await addWebsitePage(token, website.id, version.id, { slug, title });
      await loadPreview();
      setActivePageSlug(slug);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to add page.");
    } finally {
      setBusy(false);
    }
  }

  async function handleNewDraft() {
    setBusy(true);
    setError(null);
    try {
      await newWebsiteDraft(token, website.id, version.id);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to create a new draft.");
    } finally {
      setBusy(false);
    }
  }

  async function handlePublish() {
    setBusy(true);
    setError(null);
    try {
      await publishWebsiteVersion(token, website.id, version.id);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to publish. Do you have permission?");
    } finally {
      setBusy(false);
    }
  }

  async function handleUnpublish() {
    setBusy(true);
    setError(null);
    try {
      await unpublishWebsite(token, website.id);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to unpublish.");
    } finally {
      setBusy(false);
    }
  }

  async function handleThemeChange(theme: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      await updateWebsiteTheme(token, website.id, version.id, theme);
      await loadPreview();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to update theme.");
    } finally {
      setBusy(false);
    }
  }

  if (!rendered) return <Skeleton />;

  return (
    <div className="space-y-6">
      {error && (
        <div className="rounded-md border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">{error}</div>
      )}

      <div className="flex gap-2">
        {isDraft ? (
          <Button onClick={handlePublish} disabled={busy}>
            Publish this version
          </Button>
        ) : (
          <>
            <Button variant="secondary" onClick={handleNewDraft} disabled={busy}>
              Create new draft from this version
            </Button>
            {version.status === "PUBLISHED" && (
              <Button variant="danger" onClick={handleUnpublish} disabled={busy}>
                Unpublish
              </Button>
            )}
          </>
        )}
      </div>

      {isDraft && <ThemeEditor theme={rendered.theme} onSave={handleThemeChange} busy={busy} />}

      <div>
        <div className="mb-2 flex items-center gap-2">
          <Label>Pages</Label>
          {isDraft && (
            <Button variant="ghost" size="sm" onClick={handleAddPage} disabled={busy}>
              + Add page
            </Button>
          )}
        </div>
        <div className="mb-3 flex gap-2">
          {rendered.pages.map((p) => (
            <button
              key={p.slug}
              onClick={() => setActivePageSlug(p.slug)}
              className={`klaros-filter-tab ${p.slug === activePageSlug ? "klaros-filter-tab-active" : "klaros-filter-tab-inactive"}`}
            >
              {p.title}
            </button>
          ))}
        </div>
      </div>

      {activePage && editSections && (
        <div className="space-y-4">
          {editSections.map((section, i) => (
            <div key={i} className="rounded-md border border-border p-4">
              <div className="mb-2 flex items-center justify-between">
                <Badge>{section.component_type}</Badge>
                {isDraft && (
                  <div className="flex gap-1">
                    <Button variant="ghost" size="sm" disabled={i === 0} onClick={() => {
                      const next = [...editSections];
                      [next[i - 1], next[i]] = [next[i], next[i - 1]];
                      setEditSections(next);
                    }}>
                      Up
                    </Button>
                    <Button variant="ghost" size="sm" disabled={i === editSections.length - 1} onClick={() => {
                      const next = [...editSections];
                      [next[i + 1], next[i]] = [next[i], next[i + 1]];
                      setEditSections(next);
                    }}>
                      Down
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setEditSections(editSections.filter((_, j) => j !== i))}
                    >
                      Remove
                    </Button>
                  </div>
                )}
              </div>
              {isDraft ? (
                <SectionPropsForm
                  section={section}
                  onChange={(next) => {
                    const updated = [...editSections];
                    updated[i] = next;
                    setEditSections(updated);
                  }}
                />
              ) : (
                <pre className="text-xs text-muted">{JSON.stringify(section.props, null, 2)}</pre>
              )}
            </div>
          ))}

          {isDraft && (
            <div className="flex items-center gap-2">
              <AddSectionControl onAdd={(s) => setEditSections([...editSections, s])} />
              <Button onClick={saveSections} disabled={busy}>
                {busy ? "Saving..." : "Save sections"}
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function AddSectionControl({ onAdd }: { onAdd: (s: WebsiteSection) => void }) {
  const [type, setType] = useState<WebsiteSection["component_type"]>("TEXT");
  return (
    <div className="flex items-center gap-2">
      <select className="klaros-input" value={type} onChange={(e) => setType(e.target.value as WebsiteSection["component_type"])}>
        {COMPONENT_TYPES.map((t) => (
          <option key={t} value={t}>
            {t}
          </option>
        ))}
      </select>
      <Button
        variant="secondary"
        onClick={() => onAdd({ component_type: type, props: defaultPropsFor(type), data_source: defaultDataSourceFor(type) })}
      >
        + Add section
      </Button>
    </div>
  );
}

const FONT_OPTIONS = ["system-ui", "Inter", "Georgia", "Roboto", "Merriweather", "Poppins"];
const RADIUS_OPTIONS = ["NONE", "SM", "MD", "LG"];

function ThemeEditor({
  theme,
  onSave,
  busy,
}: {
  theme: Record<string, unknown>;
  onSave: (t: Record<string, unknown>) => void;
  busy: boolean;
}) {
  const [local, setLocal] = useState(theme);
  useEffect(() => setLocal(theme), [theme]);

  return (
    <details className="rounded-md border border-border p-4">
      <summary className="cursor-pointer text-sm font-medium">Theme</summary>
      <div className="mt-3 grid grid-cols-2 gap-3">
        <div>
          <Label>Primary color</Label>
          <Input type="color" value={String(local.primary_color ?? "#1A1A2E")} onChange={(e) => setLocal({ ...local, primary_color: e.target.value })} />
        </div>
        <div>
          <Label>Secondary color</Label>
          <Input type="color" value={String(local.secondary_color ?? "#E94560")} onChange={(e) => setLocal({ ...local, secondary_color: e.target.value })} />
        </div>
        <div>
          <Label>Background color</Label>
          <Input type="color" value={String(local.background_color ?? "#FFFFFF")} onChange={(e) => setLocal({ ...local, background_color: e.target.value })} />
        </div>
        <div>
          <Label>Text color</Label>
          <Input type="color" value={String(local.text_color ?? "#1A1A2E")} onChange={(e) => setLocal({ ...local, text_color: e.target.value })} />
        </div>
        <div>
          <Label>Heading font</Label>
          <select className="klaros-input" value={String(local.heading_font ?? "system-ui")} onChange={(e) => setLocal({ ...local, heading_font: e.target.value })}>
            {FONT_OPTIONS.map((f) => (
              <option key={f} value={f}>{f}</option>
            ))}
          </select>
        </div>
        <div>
          <Label>Body font</Label>
          <select className="klaros-input" value={String(local.body_font ?? "system-ui")} onChange={(e) => setLocal({ ...local, body_font: e.target.value })}>
            {FONT_OPTIONS.map((f) => (
              <option key={f} value={f}>{f}</option>
            ))}
          </select>
        </div>
        <div>
          <Label>Corner radius</Label>
          <select className="klaros-input" value={String(local.radius_scale ?? "MD")} onChange={(e) => setLocal({ ...local, radius_scale: e.target.value })}>
            {RADIUS_OPTIONS.map((r) => (
              <option key={r} value={r}>{r}</option>
            ))}
          </select>
        </div>
      </div>
      <Button className="mt-3" size="sm" disabled={busy} onClick={() => onSave(local)}>
        Save theme
      </Button>
    </details>
  );
}
