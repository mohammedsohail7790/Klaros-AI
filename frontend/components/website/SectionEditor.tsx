"use client";

/**
 * Phase 12 §6 "Editor safety": explicit, typed forms for each of the 8
 * known component types — not a generic/speculative form-generation
 * framework (deliberately out of scope per the phase spec: "if automatic
 * schema-driven forms would be excessive for this phase's scope, use
 * explicit typed forms instead"). Every field here maps 1:1 to a field on
 * the matching Props schema in
 * backend/app/schemas/website_specification.py — there is no free-text
 * field that could carry HTML/CSS/JS, and URL fields are plain strings
 * the backend re-validates against its scheme allowlist on save (this
 * editor never trusts its own validation as final).
 */

import type { WebsiteSection } from "@/lib/api";
import { Input, Label } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";

type Props = Record<string, unknown>;

export const COMPONENT_TYPES: WebsiteSection["component_type"][] = [
  "HERO",
  "TEXT",
  "CTA",
  "FEATURE_GRID",
  "PROVIDER_DIRECTORY",
  "PROCEDURE_LIST",
  "CONTACT_FORM",
  "FOOTER",
];

export function defaultPropsFor(type: WebsiteSection["component_type"]): Props {
  switch (type) {
    case "HERO":
      return { headline: "Your headline here" };
    case "TEXT":
      return { body: "Body text." };
    case "CTA":
      return { heading: "Ready to get started?", button_text: "Contact us", button_url: "https://" };
    case "FEATURE_GRID":
      return { title: "Why choose us", items: [] };
    case "PROVIDER_DIRECTORY":
      return { title: "Our providers" };
    case "PROCEDURE_LIST":
      return { title: "Our procedures" };
    case "CONTACT_FORM":
      return { title: "Contact us", fields: ["NAME", "EMAIL", "MESSAGE"], submit_label: "Send" };
    case "FOOTER":
      return { business_name: "" };
  }
}

// Deliberately generic: this editor never hardcodes a vertical's own
// provider_key (Phase 11/12 "Genericity Guard" — no vertical-name literal
// anywhere in generic Website Builder infrastructure, frontend included).
// A brand-new PROVIDER_DIRECTORY/PROCEDURE_LIST section starts with no
// data_source; the person picks which registered provider to bind it to
// via the plain text field in SectionPropsForm below (they need to know
// the key, e.g. from their vertical's own documentation — the same
// contract the backend's DataSourceRef.provider_key validates against).
export function defaultDataSourceFor(_type: WebsiteSection["component_type"]): WebsiteSection["data_source"] {
  return null;
}

function Field({ label, value, onChange, placeholder, textarea }: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  textarea?: boolean;
}) {
  return (
    <div className="space-y-1">
      <Label>{label}</Label>
      {textarea ? (
        <textarea
          className="klaros-input"
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
          rows={3}
        />
      ) : (
        <Input value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
      )}
    </div>
  );
}

function setField(props: Props, key: string, value: unknown): Props {
  return { ...props, [key]: value };
}

export function SectionPropsForm({
  section,
  onChange,
}: {
  section: WebsiteSection;
  onChange: (next: WebsiteSection) => void;
}) {
  const props = section.props ?? {};
  const update = (key: string, value: unknown) => onChange({ ...section, props: setField(props, key, value) });

  switch (section.component_type) {
    case "HERO":
      return (
        <div className="space-y-2">
          <Field label="Headline" value={String(props.headline ?? "")} onChange={(v) => update("headline", v)} />
          <Field label="Subheadline" value={String(props.subheadline ?? "")} onChange={(v) => update("subheadline", v)} textarea />
          <Field label="CTA text" value={String(props.cta_text ?? "")} onChange={(v) => update("cta_text", v)} />
          <Field label="CTA URL" value={String(props.cta_url ?? "")} onChange={(v) => update("cta_url", v)} placeholder="https://..." />
        </div>
      );
    case "TEXT":
      return (
        <div className="space-y-2">
          <Field label="Heading (optional)" value={String(props.heading ?? "")} onChange={(v) => update("heading", v)} />
          <Field label="Body" value={String(props.body ?? "")} onChange={(v) => update("body", v)} textarea />
        </div>
      );
    case "CTA":
      return (
        <div className="space-y-2">
          <Field label="Heading" value={String(props.heading ?? "")} onChange={(v) => update("heading", v)} />
          <Field label="Body (optional)" value={String(props.body ?? "")} onChange={(v) => update("body", v)} textarea />
          <Field label="Button text" value={String(props.button_text ?? "")} onChange={(v) => update("button_text", v)} />
          <Field label="Button URL" value={String(props.button_url ?? "")} onChange={(v) => update("button_url", v)} placeholder="https://..." />
        </div>
      );
    case "FEATURE_GRID": {
      const items = Array.isArray(props.items) ? (props.items as Props[]) : [];
      return (
        <div className="space-y-2">
          <Field label="Title (optional)" value={String(props.title ?? "")} onChange={(v) => update("title", v)} />
          <div className="space-y-2">
            {items.map((item, i) => (
              <div key={i} className="rounded border border-border p-2 space-y-1">
                <Field
                  label={`Feature ${i + 1} title`}
                  value={String(item.title ?? "")}
                  onChange={(v) => {
                    const next = [...items];
                    next[i] = { ...item, title: v };
                    update("items", next);
                  }}
                />
                <Field
                  label="Description"
                  value={String(item.description ?? "")}
                  onChange={(v) => {
                    const next = [...items];
                    next[i] = { ...item, description: v };
                    update("items", next);
                  }}
                  textarea
                />
                <Button variant="ghost" size="sm" onClick={() => update("items", items.filter((_, j) => j !== i))}>
                  Remove feature
                </Button>
              </div>
            ))}
            <Button
              variant="secondary"
              size="sm"
              onClick={() => update("items", [...items, { title: "New feature", icon_key: "none" }])}
            >
              Add feature
            </Button>
          </div>
        </div>
      );
    }
    case "PROVIDER_DIRECTORY":
    case "PROCEDURE_LIST":
      return (
        <div className="space-y-2">
          <Field label="Title (optional)" value={String(props.title ?? "")} onChange={(v) => update("title", v)} />
          <Field
            label="Empty-state text (optional)"
            value={String(props.empty_state_text ?? "")}
            onChange={(v) => update("empty_state_text", v)}
          />
          <Field
            label="Data source provider key (format: <namespace>.<key>)"
            value={section.data_source?.provider_key ?? ""}
            onChange={(v) =>
              onChange({
                ...section,
                data_source: v ? { provider_key: v, params: section.data_source?.params ?? {} } : null,
              })
            }
            placeholder="<namespace>.<key>"
          />
          <p className="text-xs text-muted">
            Matches a data provider registered for your active vertical (backend/app/services/website_data_providers.py) —
            this editor does not validate the key itself; the backend rejects an unknown or malformed one on save.
          </p>
        </div>
      );
    case "CONTACT_FORM": {
      const fields = Array.isArray(props.fields) ? (props.fields as string[]) : ["NAME", "EMAIL", "MESSAGE"];
      const allFields = ["NAME", "EMAIL", "PHONE", "MESSAGE"];
      return (
        <div className="space-y-2">
          <Field label="Title (optional)" value={String(props.title ?? "")} onChange={(v) => update("title", v)} />
          <Field label="Submit button label" value={String(props.submit_label ?? "Send")} onChange={(v) => update("submit_label", v)} />
          <div className="space-y-1">
            <Label>Fields</Label>
            {allFields.map((f) => (
              <label key={f} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={fields.includes(f)}
                  onChange={(e) =>
                    update("fields", e.target.checked ? [...fields, f] : fields.filter((x) => x !== f))
                  }
                />
                {f}
              </label>
            ))}
          </div>
        </div>
      );
    }
    case "FOOTER": {
      const links = Array.isArray(props.links) ? (props.links as Props[]) : [];
      return (
        <div className="space-y-2">
          <Field label="Business name" value={String(props.business_name ?? "")} onChange={(v) => update("business_name", v)} />
          <Field label="Contact email (optional)" value={String(props.contact_email ?? "")} onChange={(v) => update("contact_email", v)} />
          <Field label="Contact phone (optional)" value={String(props.contact_phone ?? "")} onChange={(v) => update("contact_phone", v)} />
          <div className="space-y-2">
            {links.map((link, i) => (
              <div key={i} className="flex gap-2">
                <Input
                  placeholder="Label"
                  value={String(link.label ?? "")}
                  onChange={(e) => {
                    const next = [...links];
                    next[i] = { ...link, label: e.target.value };
                    update("links", next);
                  }}
                />
                <Input
                  placeholder="https://..."
                  value={String(link.url ?? "")}
                  onChange={(e) => {
                    const next = [...links];
                    next[i] = { ...link, url: e.target.value };
                    update("links", next);
                  }}
                />
                <Button variant="ghost" size="sm" onClick={() => update("links", links.filter((_, j) => j !== i))}>
                  Remove
                </Button>
              </div>
            ))}
            <Button variant="secondary" size="sm" onClick={() => update("links", [...links, { label: "", url: "" }])}>
              Add link
            </Button>
          </div>
        </div>
      );
    }
    default:
      return null;
  }
}
