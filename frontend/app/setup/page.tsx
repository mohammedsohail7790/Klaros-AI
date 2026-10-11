"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { CheckCircle2, CircleDashed, Rocket, Stethoscope } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, BusinessType, MedicalTourismSetup, confirmSetupItem, getBusinessTypes, getMedicalTourismSetup, submitMedicalTourismSetup } from "@/lib/api";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";

const MODELS: { key: string; label: string; help: string }[] = [
  { key: "referral", label: "Provider referral partnerships", help: "Clinics pay an agreed fee for eligible referrals." },
  { key: "coordination", label: "Patient coordination services", help: "You are paid for permitted non-clinical coordination." },
  { key: "both", label: "Both", help: "Referral partnerships and coordination." },
];

const STATUS_LABEL: Record<string, string> = { DONE: "Done", TODO: "To do", NEEDS_INFORMATION: "Needs real information", NEEDS_APPROVAL: "Needs your confirmation" };
const DRAFT_TITLE: Record<string, string> = {
  intake_questions: "Patient intake questions",
  qualification_rule: "Qualification rule",
  pipeline: "Patient pipeline",
  consent_settings: "Consent settings",
  marketing_topics: "Marketing topic ideas",
  commercial_model: "Commercial model",
};

const list = (text: string) => text.split(/[,\n]/).map((s) => s.trim()).filter(Boolean);

/**
 * Start a business from one form (Medical Tourism first). The owner answers once; Klaros turns the module on, builds the procedure catalogue and shows a launch
 * checklist computed from what is really recorded, plus drafts clearly marked as drafts. It never invents a clinic, price, partnership or claim: what needs real
 * information or the owner's confirmation is listed as such.
 */
export default function SetupPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [types, setTypes] = useState<BusinessType[]>([]);
  const [setup, setSetup] = useState<MedicalTourismSetup | null>(null);
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(false);
  const [name, setName] = useState("");
  const [markets, setMarkets] = useState("");
  const [destinations, setDestinations] = useState("");
  const [procedures, setProcedures] = useState("");
  const [model, setModel] = useState("");

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [t, s] = await Promise.all([getBusinessTypes(token), getMedicalTourismSetup(token)]);
      setTypes(t.types);
      setSetup(s);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load setup.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  function startEditing() {
    if (setup?.profile) {
      setName(setup.profile.business_name);
      setMarkets(setup.profile.patient_markets.join(", "));
      setDestinations(setup.profile.destinations.join(", "));
      setProcedures(setup.profile.procedures.join("\n"));
      setModel(setup.profile.business_model);
    }
    setEditing(true);
  }

  async function submit() {
    if (!token) return;
    setSaving(true);
    setError(null);
    try {
      const result = await submitMedicalTourismSetup(token, { business_name: name, patient_markets: list(markets), destinations: list(destinations), procedures: list(procedures), business_model: model });
      setSetup(result);
      setEditing(false);
      try {
        sessionStorage.setItem("klaros_pilot_mode", "1");
        window.location.reload(); // the shell reads the organisation's mode on load: show the new navigation
      } catch {
        // best-effort: the page already shows the result
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to save the setup.");
    } finally {
      setSaving(false);
    }
  }

  async function confirm(key: string, confirmed: boolean) {
    if (!token) return;
    setError(null);
    try {
      setSetup(await confirmSetupItem(token, key, confirmed));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to save that.");
    }
  }

  const available = types.filter((t) => t.available);
  const showForm = !!setup && (!setup.configured || editing);
  const canSubmit = name.trim().length >= 2 && list(markets).length > 0 && list(destinations).length > 0 && list(procedures).length > 0 && !!model;

  return (
    <AppShell user={user}>
      <div className="mx-auto max-w-4xl px-6 py-8">
        <PageHeader title="Business setup" description="Describe the business once. Klaros builds the draft workspace and tells you what still needs real information or your approval." icon={Rocket} />
        {error && <Alert variant="danger" className="mb-4">{error}</Alert>}
        {authLoading || (loading && !setup) ? (
          <Skeleton />
        ) : showForm ? (
          <section aria-label="Set up a Medical Tourism business" className="klaros-card space-y-5 p-5 sm:p-6">
            <div className="flex items-start gap-3">
              <span className="mt-0.5 flex h-9 w-9 items-center justify-center rounded-lg bg-accent-soft text-accent"><Stethoscope className="h-4 w-4" aria-hidden="true" /></span>
              <div>
                <h2 className="font-display text-xl text-foreground">{available[0]?.name ?? "Medical Tourism"}</h2>
                <p className="text-sm text-muted">{available[0]?.description}</p>
              </div>
            </div>
            <label className="block text-sm">Business name
              <input aria-label="Business name" value={name} onChange={(e) => setName(e.target.value)} className="klaros-input mt-1" /></label>
            <label className="block text-sm">Where your patients live
              <input aria-label="Patient markets" value={markets} onChange={(e) => setMarkets(e.target.value)} className="klaros-input mt-1" placeholder="United Kingdom, United Arab Emirates" /></label>
            <label className="block text-sm">Where they would travel for treatment
              <input aria-label="Destinations" value={destinations} onChange={(e) => setDestinations(e.target.value)} className="klaros-input mt-1" placeholder="India, Turkey" /></label>
            <label className="block text-sm">Procedures you will offer (one per line, or separated by commas)
              <textarea aria-label="Procedures" value={procedures} onChange={(e) => setProcedures(e.target.value)} className="klaros-input mt-1 min-h-[96px]" placeholder={"Dental implants\nHair transplant"} /></label>
            <fieldset>
              <legend className="text-sm">How the business earns</legend>
              <div className="mt-1 space-y-1.5">
                {MODELS.map((m) => (
                  <label key={m.key} className="flex items-start gap-2 text-sm">
                    <input type="radio" name="model" checked={model === m.key} onChange={() => setModel(m.key)} className="mt-1" />
                    <span><span className="text-foreground">{m.label}</span> <span className="text-muted-foreground">· {m.help}</span></span>
                  </label>
                ))}
              </div>
            </fieldset>
            <div className="flex gap-2">
              <Button onClick={submit} disabled={saving || !canSubmit}>{saving ? "Creating…" : setup?.configured ? "Save changes" : "Create my business workspace"}</Button>
              {editing && <Button variant="secondary" onClick={() => setEditing(false)}>Cancel</Button>}
            </div>
            <p className="text-xs text-muted-foreground">This turns on the Medical Tourism tools and creates your procedure list. It does not publish anything, contact anyone, or add any clinic.</p>
          </section>
        ) : setup?.configured && setup.profile ? (
          <div className="space-y-8">
            <section aria-label="Your business" className="klaros-card p-5 sm:p-6">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h2 className="font-display text-xl text-foreground">{setup.profile.business_name}</h2>
                  <p className="mt-1 text-sm text-muted">{setup.profile.business_model_label} · patients in {setup.profile.patient_markets.join(", ")} · treatment in {setup.profile.destinations.join(", ")}</p>
                  <p className="mt-1 text-sm text-muted">Procedures: {setup.profile.procedures.join(", ")}</p>
                </div>
                <Button size="sm" variant="secondary" onClick={startEditing}>Edit answers</Button>
              </div>
            </section>

            <section aria-label="Launch checklist">
              <h2 className="font-display text-lg text-foreground">Launch checklist</h2>
              <p className="mt-0.5 text-sm text-muted" data-testid="checklist-summary">{setup.ready_to_launch ? "Everything on the list is in place." : `${setup.open_items} item${setup.open_items === 1 ? "" : "s"} still open.`} Klaros only marks what it can see as done.</p>
              <ul className="mt-3 divide-y divide-border rounded-md border border-border">
                {(setup.checklist ?? []).map((i) => (
                  <li key={i.key} className="flex items-start justify-between gap-3 px-4 py-3 text-sm">
                    <div className="flex items-start gap-2">
                      {i.status === "DONE" ? <CheckCircle2 className="mt-0.5 h-4 w-4 text-success" aria-hidden="true" /> : <CircleDashed className="mt-0.5 h-4 w-4 text-muted-foreground" aria-hidden="true" />}
                      <div>
                        <div className="text-foreground">{i.label}</div>
                        <div className="text-xs text-muted-foreground">{STATUS_LABEL[i.status]} · {i.why}</div>
                      </div>
                    </div>
                    <div className="shrink-0">
                      {i.route && i.status !== "DONE" && <Link href={i.route} className="text-xs underline">Open</Link>}
                      {!i.route && (
                        <label className="flex items-center gap-1.5 text-xs">
                          <input type="checkbox" aria-label={`Confirm: ${i.label}`} checked={i.status === "DONE"} onChange={(e) => confirm(i.key, e.target.checked)} /> Confirmed
                        </label>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            </section>

            <section aria-label="Drafts">
              <h2 className="font-display text-lg text-foreground">Drafts for your approval</h2>
              <p className="mt-0.5 text-sm text-muted">{setup.note}</p>
              <div className="mt-3 space-y-2">
                {Object.entries(setup.drafts ?? {}).map(([key, d]) => (
                  <details key={key} className="rounded-md border border-border px-4 py-2 text-sm">
                    <summary className="cursor-pointer text-foreground">{DRAFT_TITLE[key] ?? key} <span className="ml-2 rounded-full border border-border-strong px-2 py-0.5 text-xs text-muted">DRAFT</span></summary>
                    <div className="mt-2 text-muted">
                      {d.text && <p>{d.text}</p>}
                      {d.items && <ul className="list-disc pl-5">{d.items.map((x) => <li key={x}>{x}</li>)}</ul>}
                      {d.stages && <p>{d.stages.join(" → ")}</p>}
                      {d.scopes && <p>{d.scopes.join(", ")}</p>}
                      {d.note && <p className="mt-1 text-xs text-muted-foreground">{d.note}</p>}
                    </div>
                  </details>
                ))}
              </div>
            </section>

            <div className="flex gap-3 text-sm">
              <Link href="/medical-tourism/pipeline" className="underline">Open the patient pipeline</Link>
              <Link href="/medical-tourism/providers" className="underline">Add clinics</Link>
            </div>
          </div>
        ) : null}
      </div>
    </AppShell>
  );
}
