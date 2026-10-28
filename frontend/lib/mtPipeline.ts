/**
 * The hospital pipeline: every clinic in the order it has to be signed. The stage and the next step for each clinic are derived by the backend
 * (mt_revenue.onboarding_stage); this file only arranges them into columns and writes one summary sentence. It decides nothing.
 */
import type { ClinicAgreementRow, OnboardingStage } from "@/lib/api";

export const PIPELINE_STAGES: { key: OnboardingStage; label: string; hint: string }[] = [
  { key: "needs_contact", label: "Needs a contact", hint: "No email the hospital publishes itself yet" },
  { key: "ready_to_invite", label: "Ready to contact", hint: "A contact exists; the introduction is ready" },
  { key: "awaiting_reply", label: "Waiting for a reply", hint: "The introduction went out" },
  { key: "needs_terms", label: "Needs terms", hint: "Agree the commission" },
  { key: "needs_agreement", label: "Needs agreement", hint: "Terms agreed, agreement not on record" },
  { key: "needs_verification", label: "Needs verification", hint: "A person checks the licence and accreditation" },
  { key: "live", label: "Ready to refer", hint: "Can receive a patient" },
];

const COUNTRY: Record<string, string> = { TR: "Türkiye", TH: "Thailand", IN: "India", KR: "South Korea", CN: "China", AE: "United Arab Emirates", SA: "Saudi Arabia", MX: "Mexico", PL: "Poland", US: "United States" };
/** A readable country name for a two-letter code; anything unknown is shown as it came. */
export const countryName = (c: string): string => COUNTRY[c.toUpperCase()] ?? c;

export type PipelineRow = ClinicAgreementRow & { stage: OnboardingStage; group: string | null };

/** The first word of a clinic's name when at least two clinics share it ("Acibadem Atakent", "Acibadem Maslak"), so a group with one published contact is easy to spot. */
export function withGroups(rows: ClinicAgreementRow[]): PipelineRow[] {
  const first = (n: string) => n.trim().split(/\s+/)[0] ?? "";
  const counts = new Map<string, number>();
  for (const r of rows) counts.set(first(r.name).toLowerCase(), (counts.get(first(r.name).toLowerCase()) ?? 0) + 1);
  return rows
    .filter((r): r is ClinicAgreementRow & { stage: OnboardingStage } => !!r.stage)
    .map((r) => {
      const w = first(r.name);
      const shared = w.length >= 4 && (counts.get(w.toLowerCase()) ?? 0) >= 2;
      return { ...r, group: shared ? w : null };
    });
}

export function countries(rows: { country: string }[]): string[] {
  return Array.from(new Set(rows.map((r) => r.country).filter(Boolean))).sort();
}

/** Rows for one column, optionally for one country, in name order. */
export function column(rows: PipelineRow[], stage: OnboardingStage, country: string): PipelineRow[] {
  return rows.filter((r) => r.stage === stage && (country === "all" || r.country === country)).sort((a, b) => a.name.localeCompare(b.name));
}

/** The one sentence above the board: what is stopping the first referral. */
export function pipelineSummary(rows: PipelineRow[]): { headline: string; detail: string } {
  const n = (s: OnboardingStage) => rows.filter((r) => r.stage === s).length;
  if (rows.length === 0) return { headline: "No hospitals are listed yet.", detail: "Add a hospital on the Providers page and it appears here." };
  const live = n("live");
  if (live > 0) return { headline: `${live} ${live === 1 ? "hospital is" : "hospitals are"} ready to receive a patient.`, detail: `${n("needs_verification")} more need verification and ${n("needs_terms") + n("needs_agreement")} need terms.` };
  const ready = n("ready_to_invite");
  const waiting = n("awaiting_reply");
  const none = n("needs_contact");
  return {
    headline: "Nothing can be referred yet: no hospital is ready to receive a patient.",
    detail: `${ready} ${ready === 1 ? "has" : "have"} a contact and ${ready === 1 ? "is" : "are"} ready to be contacted, ${waiting} ${waiting === 1 ? "is" : "are"} waiting for a reply, and ${none} still ${none === 1 ? "needs" : "need"} a contact.`,
  };
}
