"use client";

/**
 * The rendered website itself: header, navigation and the active page's sections. Shared by the public runtime (app/w/[tenantId]) and the signed-in
 * draft preview (app/website/preview) so a draft looks exactly like what will go live. Rendering still goes through ComponentRegistry only — nothing
 * here interprets a component_type. All theme values arrive as CSS custom properties; styles are in ./site.css.
 */

import { Menu, X } from "lucide-react";
import { useState } from "react";
import { RenderedWebsite } from "@/lib/api";
import { RenderSection, SELF_TONED } from "@/components/website/ComponentRegistry";
import { SITE_FONT_CLASSES, fontStack } from "@/lib/siteFonts";
import "@/components/website/site.css";

const SECTION_Y: Record<string, string> = { COMPACT: "3rem", COMFORTABLE: "4.5rem", SPACIOUS: "6rem" };
const RADIUS_PX: Record<string, string> = { NONE: "0px", SM: "4px", MD: "8px", LG: "16px" };
const SHADOWS: Record<string, string> = {
  NONE: "none",
  SM: "0 1px 2px rgba(0,0,0,.06), 0 4px 14px rgba(0,0,0,.06)",
  MD: "0 2px 4px rgba(0,0,0,.08), 0 12px 32px rgba(0,0,0,.12)",
};

export function themeStyle(theme: Record<string, unknown>): React.CSSProperties {
  const style: Record<string, string> = {};
  if (typeof theme.primary_color === "string") style["--klaros-site-primary"] = theme.primary_color;
  if (typeof theme.secondary_color === "string") style["--klaros-site-secondary"] = theme.secondary_color;
  if (typeof theme.background_color === "string") style["--klaros-site-bg"] = theme.background_color;
  if (typeof theme.surface_color === "string") style["--klaros-site-surface"] = theme.surface_color;
  if (typeof theme.text_color === "string") style["--klaros-site-text"] = theme.text_color;
  if (typeof theme.heading_font === "string") style["--klaros-site-heading-font"] = fontStack(theme.heading_font);
  if (typeof theme.body_font === "string") style["--klaros-site-body-font"] = fontStack(theme.body_font);
  if (typeof theme.radius_scale === "string" && RADIUS_PX[theme.radius_scale]) style["--klaros-site-radius"] = RADIUS_PX[theme.radius_scale];
  if (typeof theme.spacing_scale === "string" && SECTION_Y[theme.spacing_scale]) style["--klaros-site-section-y"] = SECTION_Y[theme.spacing_scale];
  if (typeof theme.shadow_scale === "string" && SHADOWS[theme.shadow_scale]) style["--klaros-site-shadow"] = SHADOWS[theme.shadow_scale];
  if (typeof theme.container_width_px === "number") style["--klaros-site-max-width"] = `${theme.container_width_px}px`;
  return style as React.CSSProperties;
}

export function SiteMessage({ children }: { children: React.ReactNode }) {
  return (
    <div className={`klaros-site-root ${SITE_FONT_CLASSES}`}>
      <div style={{ maxWidth: 640, margin: "4rem auto", padding: "0 1.5rem", textAlign: "center" }}>
        <p>{children}</p>
      </div>
    </div>
  );
}

function monogram(name: string): string {
  const ch = Array.from(name.trim())[0];
  return ch ? ch.toLocaleUpperCase() : "•";
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
  const [menuOpen, setMenuOpen] = useState(false);
  const page = site.pages.find((p) => p.slug === pageSlug) ?? site.pages[0];
  if (!page) {
    return <SiteMessage>{preview ? "This version has no pages yet." : "This website has no published pages yet."}</SiteMessage>;
  }
  const theme = site.theme as Record<string, unknown>;
  const wordmark = (typeof theme.logo_text === "string" && theme.logo_text.trim()) || site.seo_defaults?.title || "";
  const home = site.navigation.items[0]?.page_slug ?? site.pages[0]?.slug ?? "home";
  const contactItem = site.navigation.items.find((i) => i.page_slug === "contact");
  const contactHref = contactItem && page.slug !== "contact" ? hrefForPage(contactItem.page_slug) : undefined;
  let neutral = 0; // counts the blocks that take the light/tinted alternation; the first one after the banner is light

  return (
    <div
      className={`klaros-site-root ${SITE_FONT_CLASSES}`}
      style={themeStyle(theme)}
      dir={theme.text_direction === "RTL" ? "rtl" : "ltr"}
      data-button={typeof theme.button_variant === "string" ? theme.button_variant : "SOLID"}
    >
      <a className="klaros-site-skip" href="#site-main">
        Skip to content
      </a>
      <header className="klaros-site-header">
        <div className="klaros-site-inner klaros-site-header-inner">
          <a className="klaros-site-logo" href={hrefForPage(home)}>
            <span className="klaros-site-logo-mark" aria-hidden="true">
              {monogram(wordmark)}
            </span>
            {wordmark ? <span className="klaros-site-logo-text">{wordmark}</span> : null}
          </a>
          {site.navigation.items.length > 0 ? (
            <>
              <button
                type="button"
                className="klaros-site-menu-button"
                aria-expanded={menuOpen}
                aria-controls="site-nav"
                onClick={() => setMenuOpen((o) => !o)}
              >
                {menuOpen ? <X size={18} aria-hidden="true" /> : <Menu size={18} aria-hidden="true" />}
                Menu
              </button>
              <nav id="site-nav" className="klaros-site-nav" data-open={menuOpen} aria-label="Main">
                {site.navigation.items.map((item, i) => (
                  <a
                    key={i}
                    href={hrefForPage(item.page_slug)}
                    aria-current={item.page_slug === page.slug ? "page" : undefined}
                    onClick={() => setMenuOpen(false)}
                  >
                    {item.label}
                  </a>
                ))}
              </nav>
            </>
          ) : null}
        </div>
      </header>
      <main id="site-main">
        {page.sections.map((section, i) => {
          const alt = !SELF_TONED.has(section.component_type) && neutral++ % 2 === 1;
          return <RenderSection key={i} section={section} tenantId={tenantId} preview={preview} alt={alt} contactHref={contactHref} />;
        })}
      </main>
    </div>
  );
}
