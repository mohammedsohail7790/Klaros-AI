"use client";

/**
 * The rendered website itself: theme, navigation and the active page's
 * sections. Shared by the public runtime (app/w/[tenantId]) and the
 * signed-in draft preview (app/website/preview) so a draft looks exactly
 * like what will go live. Rendering still goes through ComponentRegistry
 * only — nothing here interprets a component_type.
 */

import { RenderedWebsite } from "@/lib/api";
import { RenderSection } from "@/components/website/ComponentRegistry";

export function themeStyle(theme: Record<string, unknown>): React.CSSProperties {
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

export function SiteMessage({ children }: { children: React.ReactNode }) {
  return (
    <div className="klaros-site-root">
      <div style={{ maxWidth: 640, margin: "4rem auto", padding: "0 1.5rem", textAlign: "center" }}>
        <p>{children}</p>
      </div>
    </div>
  );
}

export function SiteView({
  site,
  tenantId,
  pageSlug,
  hrefForPage,
  preview = false,
}: {
  site: RenderedWebsite;
  tenantId: string;
  pageSlug: string | null;
  hrefForPage: (slug: string) => string;
  preview?: boolean;
}) {
  const page = site.pages.find((p) => p.slug === pageSlug) ?? site.pages[0];
  if (!page) {
    return <SiteMessage>{preview ? "This version has no pages yet." : "This website has no published pages yet."}</SiteMessage>;
  }
  return (
    <div className="klaros-site-root" style={themeStyle(site.theme)}>
      {site.navigation.items.length > 0 ? (
        <nav className="klaros-site-nav">
          {site.navigation.items.map((item, i) => (
            <a key={i} href={hrefForPage(item.page_slug)}>
              {item.label}
            </a>
          ))}
        </nav>
      ) : null}
      <main>
        {page.sections.map((section, i) => (
          <RenderSection key={i} section={section} tenantId={tenantId} preview={preview} />
        ))}
      </main>
    </div>
  );
}
