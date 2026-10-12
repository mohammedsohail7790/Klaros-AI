"use client";

/**
 * Signed-in preview of one website version, draft or not, rendered exactly
 * as the public site would be. It reads the authenticated preview endpoint,
 * so a draft is never reachable without the owner's own session, and the
 * enquiry form is inert here — nothing is sent until the site is published.
 */

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useAuth } from "@/lib/useAuth";
import { ApiError, RenderedWebsite, getMyWebsite, listWebsiteVersions, previewWebsiteVersion } from "@/lib/api";
import { SiteMessage, SiteView } from "@/components/website/SiteView";

export default function WebsitePreviewPage() {
  return (
    <Suspense fallback={null}>
      <WebsitePreviewInner />
    </Suspense>
  );
}

function WebsitePreviewInner() {
  const { token, user } = useAuth();
  const searchParams = useSearchParams();
  const versionId = searchParams.get("version");
  const pageSlug = searchParams.get("page");

  const [site, setSite] = useState<RenderedWebsite | null>(null);
  const [label, setLabel] = useState<string | null>(null);
  const [resolvedVersionId, setResolvedVersionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    (async () => {
      try {
        const website = await getMyWebsite(token);
        if (!website) {
          if (!cancelled) setError("No website has been generated yet.");
          return;
        }
        const versions = await listWebsiteVersions(token, website.id);
        const version = versions.find((v) => v.id === versionId) ?? versions[0];
        if (!version) {
          if (!cancelled) setError("This website has no versions yet.");
          return;
        }
        const rendered = await previewWebsiteVersion(token, website.id, version.id);
        if (cancelled) return;
        setSite(rendered);
        setResolvedVersionId(version.id);
        setLabel(version.status === "PUBLISHED" ? `v${version.version} — this is the version that is live` : `v${version.version} — ${version.status === "DRAFT" ? "draft, not public" : "not live"}`);
      } catch (err) {
        if (!cancelled) setError(err instanceof ApiError && err.status === 404 ? "That version could not be found." : "The preview could not be loaded. Please try again.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token, versionId]);

  if (error) return <SiteMessage>{error}</SiteMessage>;
  if (!site || !user) return null;

  return (
    <>
      <div
        role="status"
        style={{ position: "sticky", top: 0, zIndex: 10, display: "flex", gap: "1rem", justifyContent: "center", alignItems: "center", padding: "0.5rem 1rem", background: "#1f1b16", color: "#fff", fontSize: 13 }}
      >
        <span>Preview · {label}. The enquiry form does not send anything here.</span>
        <a href="/website" style={{ color: "#fff", textDecoration: "underline" }}>
          Back to the editor
        </a>
      </div>
      <SiteView
        site={site}
        tenantId={user.tenant_id}
        pageSlug={pageSlug}
        preview
        hrefForPage={(slug) => `?version=${encodeURIComponent(resolvedVersionId ?? "")}&page=${encodeURIComponent(slug)}`}
      />
    </>
  );
}
