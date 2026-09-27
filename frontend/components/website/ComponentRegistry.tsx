"use client";

/**
 * Phase 12 (PHASE_12_WEBSITE_BUILDER_PRODUCTIZATION_IMPLEMENTATION_LOG.md
 * §5): the frontend half of the "no second renderer" rule. This module is
 * the ONLY place a `component_type` string coming out of the backend's
 * render tree (backend/app/services/website_renderer.py) is turned into
 * anything that actually renders. It is a closed, explicit switch over the
 * same 8-value `ComponentType` enum the backend's
 * `app/schemas/website_specification.py` validates against — an unknown
 * `component_type` renders nothing (a visible "unsupported" placeholder in
 * dev, silently skipped in prod-shaped output), never a dynamic import,
 * `eval`, or `dangerouslySetInnerHTML`. Every string from the spec is
 * rendered as plain React children (JSX text), which React escapes the
 * same way it escapes any other text node — there is no HTML-from-string
 * path anywhere in this file.
 */

import { useState } from "react";
import type { RenderedSection } from "@/lib/api";
import { ApiError, submitPublicWebsiteLead } from "@/lib/api";

type Item = Record<string, unknown>;

function asItems(data: unknown): Item[] {
  if (!data || typeof data !== "object") return [];
  const items = (data as { items?: unknown }).items;
  return Array.isArray(items) ? (items.filter((i) => i && typeof i === "object") as Item[]) : [];
}

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function Hero({ props }: { props: Item }) {
  return (
    <section className="klaros-site-hero">
      <h1>{str(props.headline)}</h1>
      {props.subheadline ? <p>{str(props.subheadline)}</p> : null}
      {props.cta_text && props.cta_url ? (
        <a className="klaros-site-cta" href={str(props.cta_url)}>
          {str(props.cta_text)}
        </a>
      ) : null}
    </section>
  );
}

function Text({ props }: { props: Item }) {
  return (
    <section className="klaros-site-text">
      {props.heading ? <h2>{str(props.heading)}</h2> : null}
      <p>{str(props.body)}</p>
    </section>
  );
}

function Cta({ props }: { props: Item }) {
  return (
    <section className="klaros-site-cta-block">
      <h2>{str(props.heading)}</h2>
      {props.body ? <p>{str(props.body)}</p> : null}
      <a className="klaros-site-cta" href={str(props.button_url)}>
        {str(props.button_text)}
      </a>
    </section>
  );
}

function FeatureGrid({ props }: { props: Item }) {
  const items = Array.isArray(props.items) ? (props.items as Item[]) : [];
  return (
    <section className="klaros-site-features">
      {props.title ? <h2>{str(props.title)}</h2> : null}
      <div className="klaros-site-feature-grid">
        {items.map((item, i) => (
          <div key={i} className="klaros-site-feature">
            <h3>{str(item.title)}</h3>
            {item.description ? <p>{str(item.description)}</p> : null}
          </div>
        ))}
      </div>
    </section>
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

function ItemList({ props, data }: { props: Item; data: unknown }) {
  const items = asItems(data);
  return (
    <section className="klaros-site-items">
      {props.title ? <h2>{str(props.title)}</h2> : null}
      {items.length === 0 ? (
        <p className="klaros-site-empty">{str(props.empty_state_text) || "Nothing to show yet."}</p>
      ) : (
        <div className="klaros-site-item-grid">
          {items.map((item, i) => (
            <ItemCard key={i} item={item} />
          ))}
        </div>
      )}
    </section>
  );
}

const CONTACT_FIELD_LABELS: Record<string, string> = {
  NAME: "Name",
  EMAIL: "Email",
  PHONE: "Phone",
  MESSAGE: "Message",
};

function ContactForm({ props, tenantId }: { props: Item; tenantId: string }) {
  const fields = Array.isArray(props.fields) && props.fields.length > 0 ? (props.fields as string[]) : ["NAME", "EMAIL", "MESSAGE"];
  const [values, setValues] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (status === "sending") return;
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
      <section className="klaros-site-contact">
        {props.title ? <h2>{str(props.title)}</h2> : null}
        <p>Thanks — we received your message and will be in touch.</p>
      </section>
    );
  }

  return (
    <section className="klaros-site-contact">
      {props.title ? <h2>{str(props.title)}</h2> : null}
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
        {error ? <div className="klaros-site-form-error">{error}</div> : null}
        <button type="submit" disabled={status === "sending"}>
          {status === "sending" ? "Sending..." : str(props.submit_label) || "Send"}
        </button>
      </form>
    </section>
  );
}

function Footer({ props }: { props: Item }) {
  const links = Array.isArray(props.links) ? (props.links as Item[]) : [];
  return (
    <footer className="klaros-site-footer">
      <div>{str(props.business_name)}</div>
      {props.contact_email ? <div>{str(props.contact_email)}</div> : null}
      {props.contact_phone ? <div>{str(props.contact_phone)}</div> : null}
      {links.length > 0 ? (
        <nav>
          {links.map((l, i) => (
            <a key={i} href={str(l.url)}>
              {str(l.label)}
            </a>
          ))}
        </nav>
      ) : null}
    </footer>
  );
}

/**
 * The single entry point. `section.component_type` is whatever the
 * backend's already-validated render tree contains — this function still
 * treats it as untrusted-shaped: any value outside the known 8 renders
 * nothing rather than guessing.
 */
export function RenderSection({ section, tenantId }: { section: RenderedSection; tenantId: string }) {
  const props = (section.props ?? {}) as Item;
  switch (section.component_type) {
    case "HERO":
      return <Hero props={props} />;
    case "TEXT":
      return <Text props={props} />;
    case "CTA":
      return <Cta props={props} />;
    case "FEATURE_GRID":
      return <FeatureGrid props={props} />;
    case "PROVIDER_DIRECTORY":
    case "PROCEDURE_LIST":
      return <ItemList props={props} data={section.data} />;
    case "CONTACT_FORM":
      return <ContactForm props={props} tenantId={tenantId} />;
    case "FOOTER":
      return <Footer props={props} />;
    default:
      // Unknown component_type: render nothing. This is defense in depth —
      // backend/app/schemas/website_specification.py already rejects any
      // component_type outside the closed enum before a section can ever
      // be persisted, so this branch should be unreachable in practice.
      return null;
  }
}
