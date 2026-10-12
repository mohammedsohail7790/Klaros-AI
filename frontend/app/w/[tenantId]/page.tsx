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
import { SiteMessage, SiteView } from "@/components/website/SiteView";

export default function PublicWebsitePage() {
  return (
    <Suspense fallback={null}>
      <PublicWebsiteInner />
    </Suspense>
  );
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

  if (error || !site) return <SiteMessage>{error ?? "This website could not be found."}</SiteMessage>;

  return <SiteView site={site} tenantId={tenantId} pageSlug={requestedSlug} hrefForPage={(slug) => `?page=${encodeURIComponent(slug)}`} />;
}
