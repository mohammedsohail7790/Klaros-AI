"use client";

/**
 * Phase 12: the public, unauthenticated Website Builder runtime — the
 * first page a real site visitor (never a Klaros user) ever sees. No
 * AppShell, no useAuth, no Authorization header anywhere on this route:
 * mirrors app/quotes/view/[id]/page.tsx's "no login" pattern exactly.
 *
 * Trust boundary (matches backend/app/api/v1/public_websites.py's own
 * docstring): the `tenantId` path segment is an unguessable UUID, and the
 * backend never accepts anything else from this page as a selector — no
 * version_id, no role, no organization_id in a query string or body ever
 * leaves this component. Only the tenant's current PUBLISHED
 * WebsiteVersion is ever reachable this way; a draft is never exposed
 * here regardless of what a visitor might type into the URL.
 *
 * Rendering goes through components/website/ComponentRegistry.tsx only —
 * this file never interprets a component_type itself and never touches
 * dangerouslySetInnerHTML.
 */

import { Suspense, useEffect, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { ApiError, RenderedWebsite, getPublicWebsite } from "@/lib/api";
import { RenderSection } from "@/components/website/ComponentRegistry";

export default function PublicWebsitePage() {
  return (
    <Suspense fallback={null}>
      <PublicWebsiteInner />
    </Suspense>
  );
}

function themeStyle(theme: Record<string, unknown>): React.CSSProperties {
  const radiusPx: Record<string, string> = { NONE: "0px", SM: "4px", MD: "8px", LG: "16px" };
  const style: Record<string, string> = {};
  if (typeof theme.primary_color === "string") style["--klaros-site-primary"] = theme.primary_color;
  if (typeof theme.secondary_color === "string") style["--klaros-site-secondary"] = theme.secondary_color;
  if (typeof theme.background_color === "string") style["--klaros-site-bg"] = theme.background_color;
  if (typeof theme.text_color === "string") style["--klaros-site-text"] = theme.text_color;
  if (typeof theme.heading_font === "string") style["--klaros-site-heading-font"] = theme.heading_font;
  if (typeof theme.body_font === "string") style["--klaros-site-body-font"] = theme.body_font;
  if (typeof theme.radius_scale === "string" && radiusPx[theme.radius_scale]) {
    style["--klaros-site-radius"] = radiusPx[theme.radius_scale];
  }
  if (typeof theme.container_width_px === "number") {
    style["--klaros-site-max-width"] = `${theme.container_width_px}px`;
  }
  return style as React.CSSProperties;
}

function PublicWebsiteInner() {
  const { tenantId } = useParams<{ tenantId: string }>();
  const searchParams = useSearchParams();
  const requestedSlug = searchParams.get("page");

  const [site, setSite] = useState<RenderedWebsite | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!tenantId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    getPublicWebsite(tenantId)
      .then((result) => {
        if (!cancelled) setSite(result);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError && err.status === 404 ? "This website could not be found." : "This website is temporarily unavailable.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [tenantId]);

  useEffect(() => {
    if (site?.seo_defaults?.title) document.title = site.seo_defaults.title;
  }, [site]);

  if (loading) return null;

  if (error || !site) {
    return (
      <div className="klaros-site-root">
        <div style={{ maxWidth: 640, margin: "4rem auto", padding: "0 1.5rem", textAlign: "center" }}>
          <p>{error ?? "This website could not be found."}</p>
        </div>
      </div>
    );
  }

  const page = site.pages.find((p) => p.slug === requestedSlug) ?? site.pages[0];

  if (!page) {
    return (
      <div className="klaros-site-root">
        <div style={{ maxWidth: 640, margin: "4rem auto", padding: "0 1.5rem", textAlign: "center" }}>
          <p>This website has no published pages yet.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="klaros-site-root" style={themeStyle(site.theme)}>
      {site.navigation.items.length > 0 ? (
        <nav className="klaros-site-nav">
          {site.navigation.items.map((item, i) => (
            <a key={i} href={`?page=${encodeURIComponent(item.page_slug)}`}>
              {item.label}
            </a>
          ))}
        </nav>
      ) : null}
      <main>
        {page.sections.map((section, i) => (
          <RenderSection key={i} section={section} tenantId={tenantId} />
        ))}
      </main>
    </div>
  );
}
