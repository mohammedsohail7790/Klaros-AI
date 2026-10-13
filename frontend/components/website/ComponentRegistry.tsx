"use client";

/**
 * The frontend half of the "no second renderer" rule. This module is the ONLY place a `component_type` string coming out of the backend's render tree
 * (backend/app/services/website_renderer.py) is turned into anything that renders. It is a closed, explicit switch over the same `ComponentType` enum
 * the backend's `app/schemas/website_specification.py` validates against: an unknown `component_type` renders nothing, never a dynamic import, `eval`,
 * or `dangerouslySetInnerHTML`. Every string from the spec is rendered as plain React children (escaped like any other text node), and every link
 * target is re-checked here against http(s)/mailto/tel even though the backend already enforces the same allowlist.
 *
 * Each block renders its own full-width band (`klaros-site-band`) so SiteView can alternate tints; styles live in ./site.css.
 */

import { Check, ChevronDown, Clock, Globe, Heart, MapPin, Phone, ShieldCheck, Star } from "lucide-react";
import { useEffect, useState } from "react";
import type { PublicConsentState, RenderedSection } from "@/lib/api";
import { ApiError, getPublicConsent, submitPublicWebsiteLead } from "@/lib/api";

type Item = Record<string, unknown>;

function asItems(data: unknown): Item[] {
  if (!data || typeof data !== "object") return [];
  const items = (data as { items?: unknown }).items;
  return Array.isArray(items) ? (items.filter((i) => i && typeof i === "object") as Item[]) : [];
}

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function strs(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && x.trim() !== "") : [];
}

/** Only web, mail and phone links are ever rendered as links. */
function safeHref(v: unknown): string | undefined {
  const url = str(v).trim();
  return /^(https?:\/\/|mailto:|tel:)/i.test(url) ? url : undefined;
}

type Tone = "base" | "alt";

function Band({ tone, className, children }: { tone: string; className?: string; children: React.ReactNode }) {
  return (
    <section className={`klaros-site-band ${className ?? ""}`.trim()} data-tone={tone}>
      <div className="klaros-site-inner">{children}</div>
    </section>
  );
}

function Hero({ props, contactHref }: { props: Item; contactHref?: string }) {
  const route = strs(props.highlights);
  const cta = safeHref(props.cta_url);
  const second = safeHref(props.secondary_cta_url);
  return (
    <Band tone="hero" className="klaros-site-hero">
      <div className="klaros-site-hero-grid" data-has-route={route.length > 0}>
        <div>
          <h1>{str(props.headline)}</h1>
          {props.subheadline ? <p className="klaros-site-lead">{str(props.subheadline)}</p> : <div style={{ height: "1.5rem" }} />}
          {!(props.cta_text && cta) && contactHref ? (
            <div className="klaros-site-actions">
              <a className="klaros-site-btn" href={contactHref}>
                Get in touch
              </a>
            </div>
          ) : (props.cta_text && cta) || (props.secondary_cta_text && second) ? (
            <div className="klaros-site-actions">
              {props.cta_text && cta ? (
                <a className="klaros-site-btn" href={cta}>
                  {str(props.cta_text)}
                </a>
              ) : null}
              {props.secondary_cta_text && second ? (
                <a className="klaros-site-btn klaros-site-btn-secondary" href={second}>
                  {str(props.secondary_cta_text)}
                </a>
              ) : null}
            </div>
          ) : null}
        </div>
        {route.length > 0 ? (
          <aside className="klaros-site-route" aria-label={str(props.highlights_title) || "How it works"}>
            <h2>{str(props.highlights_title) || "How it works"}</h2>
            <ol>
              {route.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ol>
          </aside>
        ) : null}
      </div>
    </Band>
  );
}

function TrustBar({ props }: { props: Item }) {
  const items = strs(props.items);
  if (items.length === 0) return null;
  return (
    <Band tone="trust">
      <ul className="klaros-site-trust">
        {items.map((t, i) => (
          <li key={i}>
            <Check size={18} aria-hidden="true" />
            {t}
          </li>
        ))}
      </ul>
    </Band>
  );
}

function Text({ props, tone }: { props: Item; tone: Tone }) {
  return (
    <Band tone={tone}>
      {props.heading ? <h2 className="klaros-site-section-title">{str(props.heading)}</h2> : null}
      <p className="klaros-site-text-body">{str(props.body)}</p>
    </Band>
  );
}

function Cta({ props }: { props: Item }) {
  const href = safeHref(props.button_url);
  return (
    <Band tone="primary">
      <div className="klaros-site-cta-block">
        <div>
          <h2>{str(props.heading)}</h2>
          {props.body ? <p>{str(props.body)}</p> : null}
        </div>
        {href ? (
          <a className="klaros-site-btn" href={href}>
            {str(props.button_text)}
          </a>
        ) : null}
      </div>
    </Band>
  );
}

const ICONS = { check: Check, star: Star, shield: ShieldCheck, heart: Heart, globe: Globe, clock: Clock, phone: Phone, "map-pin": MapPin } as const;

function FeatureGrid({ props, tone }: { props: Item; tone: Tone }) {
  const items = Array.isArray(props.items) ? (props.items as Item[]) : [];
  return (
    <Band tone={tone}>
      {props.title ? <h2 className="klaros-site-section-title">{str(props.title)}</h2> : null}
      <div className="klaros-site-feature-grid">
        {items.map((item, i) => {
          const Icon = ICONS[str(item.icon_key) as keyof typeof ICONS];
          return (
            <div key={i} className="klaros-site-feature">
              {Icon ? (
                <span className="klaros-site-feature-icon">
                  <Icon size={20} aria-hidden="true" />
                </span>
              ) : null}
              <h3>{str(item.title)}</h3>
              {item.description ? <p>{str(item.description)}</p> : null}
            </div>
          );
        })}
      </div>
    </Band>
  );
}

function Steps({ props, tone }: { props: Item; tone: Tone }) {
  const items = Array.isArray(props.items) ? (props.items as Item[]) : [];
  if (items.length === 0) return null;
  return (
    <Band tone={tone}>
      {props.title ? <h2 className="klaros-site-section-title">{str(props.title)}</h2> : null}
      {props.intro ? <p className="klaros-site-steps-intro">{str(props.intro)}</p> : null}
      <ol className="klaros-site-steps" data-count={items.length} style={{ "--klaros-site-step-count": items.length } as React.CSSProperties}>
        {items.map((item, i) => (
          <li key={i} className="klaros-site-step">
            <h3>{str(item.title)}</h3>
            {item.description ? <p>{str(item.description)}</p> : null}
          </li>
        ))}
      </ol>
    </Band>
  );
}

function Faq({ props, tone }: { props: Item; tone: Tone }) {
  const items = (Array.isArray(props.items) ? (props.items as Item[]) : []).filter((i) => str(i.question));
  if (items.length === 0) return null;
  return (
    <Band tone={tone}>
      {props.title ? <h2 className="klaros-site-section-title">{str(props.title)}</h2> : null}
      <div className="klaros-site-faq">
        {items.map((item, i) => (
          <details key={i}>
            <summary>
              {str(item.question)}
              <ChevronDown size={20} aria-hidden="true" />
            </summary>
            <p className="klaros-site-faq-answer">{str(item.answer)}</p>
          </details>
        ))}
      </div>
    </Band>
  );
}

// Generic item card for both PROVIDER_DIRECTORY and PROCEDURE_LIST — the
// field names (name/location/description/category/offerings) are the
// data-provider registry's own generic contract
// (backend/app/services/website_data_providers.py), never a
// vertical-specific branch. A provider that returns none of these fields
// still renders safely: unknown/missing fields are simply omitted.
function ItemCard({ item }: { item: Item }) {
  const offerings = Array.isArray(item.offerings) ? (item.offerings as Item[]) : [];
  return (
    <div className="klaros-site-item-card">
      {item.name ? <h3>{str(item.name)}</h3> : null}
      {item.location ? <div className="klaros-site-item-meta">{str(item.location)}</div> : null}
      {item.category ? <div className="klaros-site-item-meta">{str(item.category)}</div> : null}
      {item.description ? <p>{str(item.description)}</p> : null}
      {offerings.length > 0 ? (
        <ul>
          {offerings.map((o, i) => (
            <li key={i}>
              {str(o.name)}
              {o.price ? ` — ${str(o.price)} ${str(o.currency)}` : ""}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function ItemList({ props, data, tone }: { props: Item; data: unknown; tone: Tone }) {
  const items = asItems(data);
  return (
    <Band tone={tone}>
      {props.title ? <h2 className="klaros-site-section-title">{str(props.title)}</h2> : null}
      {items.length === 0 ? (
        <p className="klaros-site-empty">{str(props.empty_state_text) || "Nothing to show yet."}</p>
      ) : (
        <div className="klaros-site-item-grid">
          {items.map((item, i) => (
            <ItemCard key={i} item={item} />
          ))}
        </div>
      )}
    </Band>
  );
}

const CONTACT_FIELD_LABELS: Record<string, string> = {
  NAME: "Name",
  EMAIL: "Email",
  PHONE: "Phone",
  MESSAGE: "Message",
};

function ContactForm({ props, tenantId, preview = false, tone }: { props: Item; tenantId: string; preview?: boolean; tone: Tone }) {
  const fields = Array.isArray(props.fields) && props.fields.length > 0 ? (props.fields as string[]) : ["NAME", "EMAIL", "MESSAGE"];
  const [values, setValues] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  // Whether this business must collect consent, and the exact approved words to show. Unknown (still loading, or it failed) never lets the form send.
  const [consent, setConsent] = useState<PublicConsentState | null>(null);
  const [consentFailed, setConsentFailed] = useState(false);
  const [agreed, setAgreed] = useState(false);

  useEffect(() => {
    let alive = true;
    getPublicConsent(tenantId)
      .then((c) => alive && setConsent(c))
      .catch(() => alive && setConsentFailed(true));
    return () => {
      alive = false;
    };
  }, [tenantId]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (status === "sending") return;
    if (!consent || !consent.open || (consent.required && !agreed)) {
      setStatus("error");
      setError(consent?.required ? "Please tick the box to agree before you send your enquiry." : "This form isn't ready yet. Please try again in a moment.");
      return;
    }
    if (preview) {
      // A draft preview is not live: nothing is sent and no lead is created.
      setStatus("error");
      setError("This is a preview — the form starts taking enquiries once the website is published.");
      return;
    }
    setStatus("sending");
    setError(null);
    try {
      // Tenant/site context comes ONLY from the public website's own
      // tenantId (resolved server-side from the PUBLISHED website) — the
      // visitor's browser never supplies or overrides a tenant_id.
      await submitPublicWebsiteLead(tenantId, {
        name: values.NAME || "Website visitor",
        email: values.EMAIL || undefined,
        phone: values.PHONE || undefined,
        description: values.MESSAGE || undefined,
        website: values._hp || "",
        consent: consent.required ? { scopes: ["contact", "store_personal_data"], wording_version: consent.version ?? "" } : undefined,
      });
      setStatus("sent");
      setValues({});
    } catch (err) {
      setStatus("error");
      setError(err instanceof ApiError ? err.message : "Unable to send your message. Please try again.");
    }
  }

  if (status === "sent") {
    return (
      <Band tone={tone}>
        {props.title ? <h2 className="klaros-site-section-title">{str(props.title)}</h2> : null}
        <p className="klaros-site-text-body">Thanks — we received your message and will be in touch.</p>
      </Band>
    );
  }

  if (consentFailed) {
    return (
      <Band tone={tone}>
        {props.title ? <h2 className="klaros-site-section-title">{str(props.title)}</h2> : null}
        <p className="klaros-site-empty">We can&apos;t take enquiries through this page right now. Please try again later.</p>
      </Band>
    );
  }
  if (!consent) {
    // Whether the form may open (and which consent wording it must show) is not known yet. Show a quiet placeholder of the same shape instead of
    // the fields, which for a business that has not approved a wording would appear and then vanish.
    return (
      <Band tone={tone}>
        {props.title ? <h2 className="klaros-site-section-title">{str(props.title)}</h2> : null}
        <div className="klaros-site-form-loading" role="status" aria-busy="true">
          <span className="klaros-site-sr-only">Loading the form…</span>
          <span className="klaros-site-form-loading-bar" />
          <span className="klaros-site-form-loading-bar" />
          <span className="klaros-site-form-loading-bar klaros-site-form-loading-tall" />
        </div>
      </Band>
    );
  }
  if (consent.required && !consent.open) {
    return (
      <Band tone={tone}>
        {props.title ? <h2 className="klaros-site-section-title">{str(props.title)}</h2> : null}
        <p className="klaros-site-empty">We&apos;re not taking enquiries through this page yet. Please contact us directly.</p>
      </Band>
    );
  }

  return (
    <Band tone={tone}>
      {props.title ? <h2 className="klaros-site-section-title">{str(props.title)}</h2> : null}
      <form onSubmit={handleSubmit} className="klaros-site-contact-form">
        {fields.map((f) => (
          <label key={f} className="klaros-site-contact-field">
            {CONTACT_FIELD_LABELS[f] ?? f}
            {f === "MESSAGE" ? (
              <textarea
                value={values[f] ?? ""}
                onChange={(e) => setValues((v) => ({ ...v, [f]: e.target.value }))}
                maxLength={2000}
              />
            ) : (
              <input
                type={f === "EMAIL" ? "email" : "text"}
                value={values[f] ?? ""}
                onChange={(e) => setValues((v) => ({ ...v, [f]: e.target.value }))}
                maxLength={255}
              />
            )}
          </label>
        ))}
        {/* Honeypot — hidden from real visitors via CSS, never labelled. */}
        <input
          type="text"
          aria-hidden="true"
          tabIndex={-1}
          autoComplete="off"
          className="klaros-site-honeypot"
          value={values._hp ?? ""}
          onChange={(e) => setValues((v) => ({ ...v, _hp: e.target.value }))}
        />
        {consent?.required && consent.text ? (
          <div className="klaros-site-consent" dir={consent.language === "ar" ? "rtl" : "ltr"}>
            <p className="klaros-site-consent-text">{consent.text}</p>
            <label className="klaros-site-consent-check">
              <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />
              <span>{consent.language === "ar" ? "قرأت ما سبق وأوافق عليه" : "I have read this and I agree"}</span>
            </label>
          </div>
        ) : null}
        {error ? <div className="klaros-site-form-error" role="alert">{error}</div> : null}
        <button type="submit" disabled={status === "sending" || !consent || (consent.required && !agreed)}>
          {status === "sending" ? "Sending..." : str(props.submit_label) || "Send"}
        </button>
      </form>
    </Band>
  );
}

function Footer({ props }: { props: Item }) {
  const links = Array.isArray(props.links) ? (props.links as Item[]) : [];
  return (
    <footer className="klaros-site-band" data-tone="footer">
      <div className="klaros-site-inner klaros-site-footer">
        <div className="klaros-site-footer-name">{str(props.business_name)}</div>
        {props.contact_email || props.contact_phone ? (
          <div className="klaros-site-footer-contact">
            {props.contact_email ? <span>{str(props.contact_email)}</span> : null}
            {props.contact_phone ? <span>{str(props.contact_phone)}</span> : null}
          </div>
        ) : null}
        {links.length > 0 ? (
          <nav aria-label="Footer">
            {links.map((l, i) => {
              const href = safeHref(l.url);
              return href ? (
                <a key={i} href={href}>
                  {str(l.label)}
                </a>
              ) : null;
            })}
          </nav>
        ) : null}
      </div>
    </footer>
  );
}

/**
 * The single entry point. `section.component_type` is whatever the backend's already-validated render tree contains — this function still treats it
 * as untrusted-shaped: any value outside the known set renders nothing rather than guessing. `alt` says whether this block sits on the tinted band
 * (SiteView alternates them so neighbouring blocks read as separate).
 */
export function RenderSection({ section, tenantId, preview = false, alt = false, contactHref }: { section: RenderedSection; tenantId: string; preview?: boolean; alt?: boolean; contactHref?: string }) {
  const props = (section.props ?? {}) as Item;
  const tone: Tone = alt ? "alt" : "base";
  switch (section.component_type) {
    case "HERO":
      return <Hero props={props} contactHref={contactHref} />;
    case "TRUST_BAR":
      return <TrustBar props={props} />;
    case "TEXT":
      return <Text props={props} tone={tone} />;
    case "CTA":
      return <Cta props={props} />;
    case "FEATURE_GRID":
      return <FeatureGrid props={props} tone={tone} />;
    case "STEPS":
      return <Steps props={props} tone={tone} />;
    case "FAQ":
      return <Faq props={props} tone={tone} />;
    case "PROVIDER_DIRECTORY":
    case "PROCEDURE_LIST":
      return <ItemList props={props} data={section.data} tone={tone} />;
    case "CONTACT_FORM":
      return <ContactForm props={props} tenantId={tenantId} preview={preview} tone={tone} />;
    case "FOOTER":
      return <Footer props={props} />;
    default:
      // Unknown component_type: render nothing. Defense in depth — the backend already rejects any component_type outside the closed enum
      // before a section can ever be persisted, so this branch should be unreachable in practice.
      return null;
  }
}

/** Blocks that carry their own colour (hero, trust bar, call to action, footer) do not take part in the light/tinted alternation. */
export const SELF_TONED = new Set(["HERO", "TRUST_BAR", "CTA", "FOOTER"]);
