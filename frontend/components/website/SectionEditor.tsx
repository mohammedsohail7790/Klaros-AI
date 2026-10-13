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

import { useId } from "react";
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
  "STEPS",
  "FAQ",
  "TRUST_BAR",
  "SHOWCASE",
  "SPLIT",
];

/** What each building block is called in the editor (the stored type stays the closed enum). */
export const SECTION_LABELS: Record<WebsiteSection["component_type"], string> = {
  HERO: "Headline banner",
  TEXT: "Text",
  CTA: "Call to action",
  FEATURE_GRID: "Feature grid",
  PROVIDER_DIRECTORY: "Provider list",
  PROCEDURE_LIST: "Services list",
  CONTACT_FORM: "Enquiry form",
  FOOTER: "Footer",
  STEPS: "Steps",
  FAQ: "Questions and answers",
  TRUST_BAR: "Facts bar",
  SHOWCASE: "Product / offer cards",
  SPLIT: "Text beside a picture",
};

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
    case "STEPS":
      return { title: "How it works", items: [{ title: "First step" }, { title: "Second step" }] };
    case "FAQ":
      return { title: "Questions", items: [{ question: "A question people ask", answer: "Your answer." }] };
    case "TRUST_BAR":
      return { items: ["Something true about your business"] };
    case "SHOWCASE":
      return { title: "What we offer", items: [{ title: "First product or service" }] };
    case "SPLIT":
      return { heading: "About us", body: "Tell visitors what makes your business worth choosing.", bullets: [], image_side: "RIGHT" };
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
  const id = useId(); // ties the label to its control so screen readers announce it
  return (
    <div className="space-y-1">
      <Label htmlFor={id}>{label}</Label>
      {textarea ? (
        <textarea
          id={id}
          className="klaros-input"
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
          rows={3}
        />
      ) : (
        <Input id={id} value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
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
          <Field label="Small line above the headline (optional)" value={String(props.eyebrow ?? "")} onChange={(v) => update("eyebrow", v)} />
          <Field label="Headline" value={String(props.headline ?? "")} onChange={(v) => update("headline", v)} />
          <Field label="Hero picture (optional, https:// link)" value={String(props.background_image_url ?? "")} onChange={(v) => update("background_image_url", v)} placeholder="https://… — leave empty for a designed visual" />
          <Field label="Subheadline" value={String(props.subheadline ?? "")} onChange={(v) => update("subheadline", v)} textarea />
          <Field label="CTA text" value={String(props.cta_text ?? "")} onChange={(v) => update("cta_text", v)} />
          <Field label="CTA URL" value={String(props.cta_url ?? "")} onChange={(v) => update("cta_url", v)} placeholder="https://, mailto: or tel:" />
          <Field label="Second button text (optional)" value={String(props.secondary_cta_text ?? "")} onChange={(v) => update("secondary_cta_text", v)} />
          <Field label="Second button URL" value={String(props.secondary_cta_url ?? "")} onChange={(v) => update("secondary_cta_url", v)} placeholder="https://, mailto: or tel:" />
          <Field label="Side card title (optional)" value={String(props.highlights_title ?? "")} onChange={(v) => update("highlights_title", v)} />
          <Field
            label="Side card lines (one per line, up to 5)"
            value={(Array.isArray(props.highlights) ? (props.highlights as string[]) : []).join("\n")}
            onChange={(v) => update("highlights", v.split("\n").map((x) => x.trim()).filter(Boolean).slice(0, 5))}
            textarea
          />
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
    case "STEPS": {
      const items = Array.isArray(props.items) ? (props.items as Props[]) : [];
      return (
        <div className="space-y-2">
          <Field label="Title (optional)" value={String(props.title ?? "")} onChange={(v) => update("title", v)} />
          <Field label="Introduction (optional)" value={String(props.intro ?? "")} onChange={(v) => update("intro", v)} textarea />
          {items.map((item, i) => (
            <div key={i} className="rounded border border-border p-2 space-y-1">
              <Field
                label={`Step ${i + 1} title`}
                value={String(item.title ?? "")}
                onChange={(v) => update("items", items.map((x, j) => (j === i ? { ...x, title: v } : x)))}
              />
              <Field
                label="Description (optional)"
                value={String(item.description ?? "")}
                onChange={(v) => update("items", items.map((x, j) => (j === i ? { ...x, description: v } : x)))}
                textarea
              />
              <Button variant="ghost" size="sm" onClick={() => update("items", items.filter((_, j) => j !== i))}>
                Remove step
              </Button>
            </div>
          ))}
          {items.length < 8 && (
            <Button variant="secondary" size="sm" onClick={() => update("items", [...items, { title: "New step" }])}>
              Add step
            </Button>
          )}
          <p className="text-xs text-muted">Steps are numbered in the order shown. Only describe what you really do.</p>
        </div>
      );
    }
    case "FAQ": {
      const items = Array.isArray(props.items) ? (props.items as Props[]) : [];
      return (
        <div className="space-y-2">
          <Field label="Title (optional)" value={String(props.title ?? "")} onChange={(v) => update("title", v)} />
          {items.map((item, i) => (
            <div key={i} className="rounded border border-border p-2 space-y-1">
              <Field
                label={`Question ${i + 1}`}
                value={String(item.question ?? "")}
                onChange={(v) => update("items", items.map((x, j) => (j === i ? { ...x, question: v } : x)))}
              />
              <Field
                label="Answer"
                value={String(item.answer ?? "")}
                onChange={(v) => update("items", items.map((x, j) => (j === i ? { ...x, answer: v } : x)))}
                textarea
              />
              <Button variant="ghost" size="sm" onClick={() => update("items", items.filter((_, j) => j !== i))}>
                Remove question
              </Button>
            </div>
          ))}
          {items.length < 12 && (
            <Button variant="secondary" size="sm" onClick={() => update("items", [...items, { question: "New question", answer: "Your answer." }])}>
              Add question
            </Button>
          )}
        </div>
      );
    }
    case "SHOWCASE": {
      const items = Array.isArray(props.items) ? (props.items as Props[]) : [];
      const setItem = (i: number, key: string, v: string) => {
        const next = [...items];
        next[i] = { ...items[i], [key]: v === "" && key !== "title" ? undefined : v };
        update("items", next);
      };
      return (
        <div className="space-y-2">
          <Field label="Title (optional)" value={String(props.title ?? "")} onChange={(v) => update("title", v)} />
          <Field label="Introduction (optional)" value={String(props.intro ?? "")} onChange={(v) => update("intro", v)} textarea />
          {items.map((item, i) => (
            <div key={i} className="space-y-1 rounded-lg border border-border p-3">
              <Field label={`Card ${i + 1} name`} value={String(item.title ?? "")} onChange={(v) => setItem(i, "title", v)} />
              <Field label="Description" value={String(item.description ?? "")} onChange={(v) => setItem(i, "description", v)} textarea />
              <div className="grid gap-2 sm:grid-cols-2">
                <Field label="Price (optional)" value={String(item.price ?? "")} onChange={(v) => setItem(i, "price", v)} placeholder="e.g. $39" />
                <Field label="Label (optional)" value={String(item.badge ?? "")} onChange={(v) => setItem(i, "badge", v)} placeholder="e.g. New" />
              </div>
              <Field label="Picture (optional, https:// link)" value={String(item.image_url ?? "")} onChange={(v) => setItem(i, "image_url", v)} placeholder="Leave empty for a designed tile" />
              <Button variant="ghost" size="sm" onClick={() => update("items", items.filter((_, j) => j !== i))}>
                Remove card
              </Button>
            </div>
          ))}
          <Button variant="secondary" size="sm" onClick={() => update("items", [...items, { title: "New item" }])}>
            Add card
          </Button>
        </div>
      );
    }
    case "SPLIT": {
      const bullets = Array.isArray(props.bullets) ? (props.bullets as string[]) : [];
      return (
        <div className="space-y-2">
          <Field label="Small line above the heading (optional)" value={String(props.eyebrow ?? "")} onChange={(v) => update("eyebrow", v)} />
          <Field label="Heading" value={String(props.heading ?? "")} onChange={(v) => update("heading", v)} />
          <Field label="Text" value={String(props.body ?? "")} onChange={(v) => update("body", v)} textarea />
          <Field
            label="Key points (one per line, up to 6)"
            value={bullets.join("\n")}
            onChange={(v) => update("bullets", v.split("\n").map((x) => x.trim()).filter(Boolean).slice(0, 6))}
            textarea
          />
          <Field label="Picture (optional, https:// link)" value={String(props.image_url ?? "")} onChange={(v) => update("image_url", v)} placeholder="Leave empty for a designed visual" />
          <div className="space-y-1">
            <Label>Picture side</Label>
            <select className="klaros-input" value={String(props.image_side ?? "RIGHT")} onChange={(e) => update("image_side", e.target.value)}>
              <option value="RIGHT">Right</option>
              <option value="LEFT">Left</option>
            </select>
          </div>
          <Field label="Button text (optional)" value={String(props.cta_text ?? "")} onChange={(v) => update("cta_text", v)} />
          <Field label="Button link (optional)" value={String(props.cta_url ?? "")} onChange={(v) => update("cta_url", v)} placeholder="https://, mailto: or tel:" />
        </div>
      );
    }
    case "TRUST_BAR": {
      const items = Array.isArray(props.items) ? (props.items as string[]) : [];
      return (
        <div className="space-y-2">
          <Field
            label="Facts (one per line, up to 6)"
            value={items.join("\n")}
            onChange={(v) => update("items", v.split("\n").map((x) => x.trim()).filter(Boolean).slice(0, 6))}
            textarea
          />
          <p className="text-xs text-muted">Plain facts about your business, such as the places you serve or languages you speak. Not ratings or numbers you cannot show.</p>
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
            label="Where this list comes from"
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
            Filled in for you when the website is generated — it links this list to your own records. Leave it as it is
            unless you were asked to change it; a value Klaros doesn&apos;t recognise is rejected when you save.
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
