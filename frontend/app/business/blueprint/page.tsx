"use client";

/**
 * Business Blueprint review: "this is what Klaros thinks your business is."
 *
 * Renders the 20 fixed BlueprintSection keys returned by the Blueprint API,
 * grouped into the headings a business owner thinks in. Edits and claim
 * confirm/reject go straight to the existing Blueprint endpoints and nothing
 * is final until the server confirms it. "Confirm and continue" is the
 * explicit human checkpoint and calls the journey action `confirm-blueprint`
 * — never the raw activate endpoint. Once confirmed the Blueprint is shown
 * read-only (a later edit is a new version, handled server-side).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { Button } from "@/components/ui/Button";
import { Alert } from "@/components/ui/Alert";
import { Skeleton } from "@/components/ui/Skeleton";
import { PageHeader } from "@/components/ui/PageHeader";
import { FileText, HelpCircle, Pencil } from "lucide-react";
import {
  ApiError,
  BLUEPRINT_SECTION_LABELS,
  BlueprintClaim,
  BlueprintSection,
  BlueprintSectionKey,
  BusinessJourney,
  FullBlueprintResponse,
  confirmBlueprintClaim,
  confirmBlueprintJourneyStep,
  getActiveBlueprint,
  getCurrentBusinessJourney,
  listBusinessJourneys,
  getDraftBlueprint,
  rejectBlueprintClaim,
  updateBlueprintSection,
} from "@/lib/api";
import { getJourneyDestination } from "@/lib/businessJourneyController";
import { StageTracker } from "@/components/business/StageTracker";
import type { BuilderStage } from "@/lib/api";

const GROUPS: { title: string; blurb: string; keys: BlueprintSectionKey[] }[] = [
  { title: "Business overview", blurb: "What the business is and how it makes money.", keys: ["IDENTITY", "INDUSTRY", "BUSINESS_MODEL"] },
  { title: "Customers & offerings", blurb: "Who you serve, what you offer, and where.", keys: ["CUSTOMERS", "PRODUCTS_SERVICES", "GEOGRAPHY"] },
  { title: "Revenue & acquisition", blurb: "How money comes in and how customers find you.", keys: ["REVENUE", "CHANNELS", "MARKETING", "FINANCE"] },
  { title: "Customer journey & operations", blurb: "What happens after a customer gets in touch.", keys: ["CUSTOMER_JOURNEY", "OPERATIONS", "COMMUNICATIONS", "SUPPLIERS_PROVIDERS", "COMPLIANCE"] },
  { title: "Required capabilities", blurb: "What the business needs in order to operate.", keys: ["REQUIRED_CAPABILITIES"] },
  { title: "Goals, assumptions & decisions", blurb: "Where you're headed, and what we've assumed along the way.", keys: ["GOALS", "ASSUMPTIONS", "CONSTRAINTS", "DECISIONS"] },
];

const CLAIM_KIND: Record<string, string> = {
  Fact: "You said",
  Preference: "You prefer",
  Constraint: "Constraint",
  Decision: "Decision",
  Inference: "Klaros inferred",
  Requirement: "Klaros inferred a need",
  Assumption: "Assumed",
  Unknown: "Still unknown",
};

const BLUEPRINT_STAGES: BuilderStage[] = [
  { key: "idea", label: "Idea", state: "done", route: "/business" },
  { key: "discovery", label: "Discovery", state: "done", route: "/business/discovery" },
  { key: "blueprint", label: "Blueprint", state: "current", route: "/business/blueprint" },
  { key: "requirements", label: "Requirements", state: "todo", route: "/business/requirements" },
  { key: "recommendations", label: "Recommendations", state: "todo", route: "/business/recommendations" },
  { key: "map", label: "Business Map", state: "todo", route: "/business/map" },
  { key: "website", label: "Website", state: "todo", route: "/website" },
  { key: "launch", label: "Launch", state: "todo", route: "/business/home" },
];

// Activation needs these four sections filled (the backend enforces it); naming them
// up front means "Confirm" never fails with a mystery.
const REQUIRED_SECTIONS: BlueprintSectionKey[] = ["IDENTITY", "INDUSTRY", "BUSINESS_MODEL", "REQUIRED_CAPABILITIES"];

const humanize = (k: string) => k.replace(/[._]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
/** Human-readable rendering of any stored value — never "null", "True" or raw JSON. */
const show = (v: unknown): string => {
  if (v === null || v === undefined || v === "") return "Not specified";
  if (v === true) return "Yes";
  if (v === false) return "No";
  if (Array.isArray(v)) return v.map(show).join(", ");
  if (typeof v === "object")
    return Object.entries(v as Record<string, unknown>)
      .map(([k, x]) => `${humanize(k)}: ${show(x)}`)
      .join(" · ");
  return String(v);
};

const SOURCE: Record<string, string> = {
  USER_STATED: "from your answers",
  AI_INFERRED: "inferred by Klaros",
  SYSTEM_DEFAULT: "assumed by Klaros",
};

/** How a claim reads to a person: a per-capability flag shows its name, an
 * empty value says so instead of printing "null". */
function claimText(c: BlueprintClaim): string {
  const v = c.value;
  if (v === true) return humanize(c.key.split(".").pop() ?? c.key);
  if (v === null || v === undefined || v === "") return `${humanize(c.key.split(".").pop() ?? c.key)} — not specified yet`;
  return show(v);
}

export default function BlueprintPage() {
  const { token, user } = useAuth();
  const router = useRouter();
  const [journey, setJourney] = useState<BusinessJourney | null | undefined>(undefined);
  const [data, setData] = useState<FullBlueprintResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const confirming = useRef(false);

  const load = useCallback(async () => {
    if (!token) return;
    setError(null);
    try {
      let j = await getCurrentBusinessJourney(token);
      if (!j) {
        // A finished journey still has a confirmed Blueprint worth viewing.
        const latest = (await listBusinessJourneys(token))?.[0];
        if (latest?.status === "COMPLETED") j = latest;
      }
      if (!j) {
        router.replace("/business");
        return;
      }
      if (j.status === "DISCOVERY_ACTIVE") {
        router.replace(getJourneyDestination(j.status));
        return;
      }
      setJourney(j);
      setData(j.status === "BLUEPRINT_REVIEW" ? await getDraftBlueprint(token) : await getActiveBlueprint(token));
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.push("/login");
        return;
      }
      setError(err instanceof ApiError ? err.message : "We couldn't load your Business Blueprint. Check your connection and try again.");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleConfirmBlueprint() {
    if (!token || !journey || confirming.current) return;
    confirming.current = true;
    setBusy(true);
    setError(null);
    try {
      const updated = await confirmBlueprintJourneyStep(token, journey.id);
      router.replace(getJourneyDestination(updated.status));
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        await load();
        setError(
          "Klaros needs a little more before this can be confirmed — answer “Is this right?” on the highlighted statements, and make sure the business overview and required capabilities have something in them."
        );
      } else {
        setError(err instanceof ApiError ? err.message : "We couldn't confirm your Blueprint. Please try again.");
      }
      setBusy(false);
    } finally {
      confirming.current = false;
    }
  }

  async function handleClaimDecision(claim: BlueprintClaim, decision: "confirm" | "reject") {
    if (!token || busy) return;
    setBusy(true);
    setError(null);
    try {
      if (decision === "confirm") await confirmBlueprintClaim(token, claim.id);
      else await rejectBlueprintClaim(token, claim.id);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "We couldn't update that. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  async function handleConfirmAll() {
    if (!token || !data || busy) return;
    setBusy(true);
    setError(null);
    try {
      for (const c of data.claims.filter((x) => x.status === "PROPOSED")) await confirmBlueprintClaim(token, c.id);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "We couldn't confirm those. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  async function handleSectionSave(section: BlueprintSection, nextData: Record<string, unknown>) {
    if (!token || !data || busy) return;
    setBusy(true);
    setError(null);
    try {
      await updateBlueprintSection(token, section.section_key, data.blueprint.id, nextData);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "We couldn't save that section. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  if (journey === undefined || (journey && !data)) {
    return (
      <AppShell user={user}>
        <div className="mx-auto max-w-4xl px-6 py-10">
          {error ? <Alert variant="danger" action={<Button size="sm" variant="secondary" onClick={load}>Retry</Button>}>{error}</Alert> : <Skeleton rows={6} />}
        </div>
      </AppShell>
    );
  }
  if (!data || !journey) return null;

  const isDraft = data.blueprint.status === "DRAFT";
  const claimsBySection = data.claims.reduce<Record<string, BlueprintClaim[]>>((acc, c) => {
    (acc[c.section_key] ??= []).push(c);
    return acc;
  }, {});
  const sectionByKey = Object.fromEntries(data.sections.map((s) => [s.section_key, s]));
  const unknowns = data.claims.filter((c) => c.claim_type === "Unknown" && c.status !== "REJECTED");
  const assumptions = data.claims.filter((c) => c.claim_type === "Assumption" && c.status !== "REJECTED");
  const toConfirm = data.claims.filter((c) => c.status === "PROPOSED").length;
  const missingRequired = isDraft
    ? REQUIRED_SECTIONS.filter((k) => sectionByKey[k] && sectionByKey[k].status !== "COMPLETE").map(
        (k) => BLUEPRINT_SECTION_LABELS[k] ?? humanize(k)
      )
    : [];

  return (
    <AppShell user={user}>
      <div className="mx-auto max-w-4xl px-6 py-8">
        <StageTracker stages={BLUEPRINT_STAGES} activeKey="blueprint" />
        <PageHeader
          icon={FileText}
          title="Here's what Klaros thinks your business is"
          description={isDraft ? "Check it over, fix anything that's off, then confirm. Nothing is final until you do." : "Confirmed. This is the foundation everything else is built from."}
          actions={
            isDraft ? (
              <Button onClick={handleConfirmBlueprint} disabled={busy}>
                {busy ? "Confirming…" : "Confirm and continue"}
              </Button>
            ) : (
              <Link href={getJourneyDestination(journey.status) === "/business/blueprint" ? "/business/requirements" : getJourneyDestination(journey.status)} className="klaros-btn-primary">
                Continue to requirements
              </Link>
            )
          }
        />

        {error && <div className="mb-4"><Alert variant="danger">{error}</Alert></div>}
        {missingRequired.length > 0 && toConfirm === 0 && (
          <div className="mb-4">
            <Alert variant="warning">
              Before you can continue, add something for: <strong className="font-medium">{missingRequired.join(", ")}</strong>. Use
              “Edit” on {missingRequired.length === 1 ? "that section" : "those sections"} below.
            </Alert>
          </div>
        )}
        {isDraft && toConfirm > 0 && (
          <div className="mb-4">
            <Alert
              variant="info"
              action={<Button size="sm" variant="secondary" disabled={busy} onClick={handleConfirmAll}>Everything looks right — confirm all</Button>}
            >
              {toConfirm} statement{toConfirm === 1 ? "" : "s"} below {toConfirm === 1 ? "is" : "are"} waiting for you to confirm or reject.
            </Alert>
          </div>
        )}

        <div className="space-y-10">
          {GROUPS.map((g) => (
            <section key={g.title} aria-labelledby={`bp-${g.title}`}>
              <h2 id={`bp-${g.title}`} className="font-display text-xl text-foreground">{g.title}</h2>
              <p className="mb-3 text-sm text-muted">{g.blurb}</p>
              <div className="space-y-3">
                {g.keys.map((k) => {
                  const section = sectionByKey[k];
                  if (!section) return null;
                  return (
                    <SectionCard
                      key={section.id}
                      section={section}
                      claims={claimsBySection[k] ?? []}
                      editable={isDraft}
                      busy={busy}
                      onSave={(next) => handleSectionSave(section, next)}
                      onClaimDecision={handleClaimDecision}
                    />
                  );
                })}
              </div>
            </section>
          ))}

          <section aria-labelledby="bp-unknowns">
            <h2 id="bp-unknowns" className="flex items-center gap-2 font-display text-xl text-foreground">
              <HelpCircle className="h-5 w-5 text-warning" aria-hidden="true" /> Still unresolved
            </h2>
            {unknowns.length === 0 && assumptions.length === 0 ? (
              <p className="mt-2 text-sm text-muted">Nothing is marked as unknown or assumed.</p>
            ) : (
              <ul className="mt-3 space-y-2 text-sm">
                {[...unknowns, ...assumptions].map((c) => (
                  <li key={c.id} className="klaros-card px-4 py-3">
                    <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{CLAIM_KIND[c.claim_type] ?? c.claim_type}</span>
                    <p className="text-foreground">{claimText(c)}</p>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>
    </AppShell>
  );
}

function SectionCard({
  section,
  claims,
  editable,
  busy,
  onSave,
  onClaimDecision,
}: {
  section: BlueprintSection;
  claims: BlueprintClaim[];
  editable: boolean;
  busy: boolean;
  onSave: (data: Record<string, unknown>) => void;
  onClaimDecision: (claim: BlueprintClaim, decision: "confirm" | "reject") => void;
}) {
  const label = BLUEPRINT_SECTION_LABELS[section.section_key as BlueprintSectionKey] ?? humanize(section.section_key);
  const [editing, setEditing] = useState(false);
  const entries = Object.entries(section.data ?? {});
  const [fields, setFields] = useState<Record<string, string>>({});
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const proposed = claims.filter((c) => c.status === "PROPOSED");
  const settled = claims.filter((c) => c.status === "CONFIRMED" && c.claim_type !== "Unknown" && c.claim_type !== "Assumption");

  function startEdit() {
    const initial = Object.fromEntries(entries.map(([k, v]) => [k, Array.isArray(v) ? v.map(show).join("\n") : typeof v === "object" && v !== null ? JSON.stringify(v) : String(v)]));
    // The capability list is what Requirements are derived from, so it is always offered.
    if (section.section_key === "REQUIRED_CAPABILITIES" && !("capabilities" in initial)) initial.capabilities = "";
    setFields(initial);
    setNewKey("");
    setNewValue("");
    setEditing(true);
  }

  function save() {
    const next: Record<string, unknown> = {};
    const original = section.data ?? {};
    for (const [k, v] of Object.entries(fields)) {
      const orig = original[k];
      if (section.section_key === "REQUIRED_CAPABILITIES" && k === "capabilities") {
        const list = v.split(/\n|,/).map((x) => x.trim()).filter(Boolean);
        if (list.length > 0 || Array.isArray(orig)) next[k] = list;
      } else if (Array.isArray(orig)) next[k] = v.split("\n").map((x) => x.trim()).filter(Boolean);
      else if (orig !== null && typeof orig === "object") {
        try { next[k] = JSON.parse(v); } catch { next[k] = orig; }
      } else next[k] = v;
    }
    if (newKey.trim() && newValue.trim()) next[newKey.trim().toLowerCase().replace(/\s+/g, "_")] = newValue.trim();
    onSave(next);
    setEditing(false);
  }

  return (
    <div className="klaros-card p-4 sm:p-5">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-foreground">{label}</h3>
        {editable && !editing && (
          <Button variant="ghost" size="sm" onClick={startEdit} className="gap-1" aria-label={`Edit ${label}`}>
            <Pencil className="h-3.5 w-3.5" aria-hidden="true" /> Edit
          </Button>
        )}
      </div>

      {editing ? (
        <div className="space-y-3">
          {Object.keys(fields).map((k) => (
            <div key={k}>
              <label htmlFor={`${section.id}-${k}`} className="klaros-label">
                {humanize(k)}
                {section.section_key === "REQUIRED_CAPABILITIES" && k === "capabilities" ? " — one per line (these become your requirements)" : ""}
              </label>
              <textarea
                id={`${section.id}-${k}`}
                className="klaros-input min-h-[60px]"
                value={fields[k]}
                onChange={(e) => setFields((f) => ({ ...f, [k]: e.target.value }))}
              />
            </div>
          ))}
          <div className="grid gap-2 sm:grid-cols-[1fr_2fr]">
            <input className="klaros-input" placeholder="Add a detail (name)" value={newKey} onChange={(e) => setNewKey(e.target.value)} aria-label="New detail name" />
            <input className="klaros-input" placeholder="Value" value={newValue} onChange={(e) => setNewValue(e.target.value)} aria-label="New detail value" />
          </div>
          <div className="flex gap-2">
            <Button size="sm" onClick={save} disabled={busy}>Save</Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
          </div>
        </div>
      ) : entries.length > 0 && entries.every(([, v]) => v === true) ? (
        <ul className="flex flex-wrap gap-2" aria-label={`${label} listed`}>
          {entries.map(([k]) => (
            <li key={k} className="rounded-full border border-border bg-surface-muted px-2.5 py-0.5 text-sm text-foreground">{humanize(k)}</li>
          ))}
        </ul>
      ) : entries.length > 0 ? (
        <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          {entries.map(([k, v]) => (
            <div key={k}>
              <dt className="klaros-label">{humanize(k)}</dt>
              <dd className="text-foreground">{show(v)}</dd>
            </div>
          ))}
        </dl>
      ) : settled.length === 0 && proposed.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          <span className="mr-1.5 rounded border border-border px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-wide">Missing</span>
          Klaros doesn't know this yet{editable ? " — add what you know, or leave it for later." : "."}
        </p>
      ) : null}

      {settled.length > 0 && (
        <ul className="mt-3 space-y-1 border-t border-border pt-3 text-sm">
          {settled.map((c) => (
            <li key={c.id}>
              <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{CLAIM_KIND[c.claim_type] ?? c.claim_type} · </span>
              <span className="text-foreground">{claimText(c)}</span>
              {SOURCE[c.provenance] && <span className="ml-1.5 text-xs text-muted-foreground">({SOURCE[c.provenance]})</span>}
            </li>
          ))}
        </ul>
      )}

      {proposed.length > 0 && (
        <div className="mt-3 space-y-2 border-t border-border pt-3">
          <p className="klaros-label">Is this right?</p>
          {proposed.map((c) => (
            <div key={c.id} className="flex flex-col gap-2 rounded-md border border-border px-3 py-2 text-sm sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <span className="text-xs text-muted-foreground">{CLAIM_KIND[c.claim_type] ?? c.claim_type} · </span>
                <span className="text-foreground">{claimText(c)}</span>
                {SOURCE[c.provenance] && <span className="ml-1.5 text-xs text-muted-foreground">({SOURCE[c.provenance]})</span>}
              </div>
              <div className="flex shrink-0 gap-1">
                <Button size="sm" variant="secondary" disabled={busy} onClick={() => onClaimDecision(c, "confirm")}>Yes</Button>
                <Button size="sm" variant="ghost" disabled={busy} onClick={() => onClaimDecision(c, "reject")}>No</Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
