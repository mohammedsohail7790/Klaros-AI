/**
 * What the owner should do next, worked out from the recorded facts the backend already derives (pipeline stage, referrals, open tasks).
 * Nothing here decides anything: it only turns facts into a prioritised, plain-language to-do list, so the first screen answers "what needs me today?".
 */
import type { HandoffTask, PipelineLead } from "@/lib/api";
import { TASK_KIND_LABEL, TASK_REASON_LABEL } from "@/lib/mtLabels";

export const WITHHELD_NAME = "Patient (details withheld)";
export const patientName = (l: Pick<PipelineLead, "name">): string => l.name ?? WITHHELD_NAME;
export const caseHref = (leadId: string): string => `/medical-tourism/patients/${leadId}`;

const DAY = 24 * 60 * 60 * 1000;

/** "today", "yesterday", "3 days ago", "2 weeks ago". Empty for a missing or unreadable time. */
export function ageLabel(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const days = Math.floor((now - t) / DAY);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 14) return `${days} days ago`;
  return `${Math.floor(days / 7)} weeks ago`;
}

const daysSince = (iso: string | null | undefined, now: number): number | null => {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : Math.floor((now - t) / DAY);
};

/** A clinic that has not answered for this many days is worth chasing. */
export const CHASE_AFTER_DAYS = 3;

export interface NextStep {
  /** The one button for this patient, or null when there is nothing for a person to do. */
  cta: string | null;
  /** One sentence on why. */
  hint: string;
  priority: 1 | 2 | 3 | 4;
}

const currentReferral = (l: PipelineLead) => l.referrals.find((r) => r.status !== "CANCELLED") ?? null;

/** The single next step for a patient, from where the recorded facts put them. Priority 1 is most urgent, 4 means nothing to do now. */
export function nextStepFor(l: PipelineLead, now: number = Date.now()): NextStep {
  const ref = currentReferral(l);
  const clinic = ref?.provider_name ?? "the clinic";
  // A patient who withdrew consent must never be pointed at "send": nothing more goes out, and anything already sent has to be taken back.
  if (l.consent_frozen && ref) {
    const sent = ["SENT", "ACCEPTED", "DECLINED", "OUTCOME_REPORTED"].includes(ref.status);
    return {
      cta: "Review the referral",
      hint: sent
        ? `The patient withdrew consent after their details were sent to ${clinic}. Ask the clinic to remove them, and record it.`
        : `The patient withdrew consent. Nothing will be sent to ${clinic}. Open the referral and record that they declined.`,
      priority: 1,
    };
  }
  switch (l.stage) {
    case "NEW_ENQUIRY":
      return { cta: "Record consent", hint: "No consent decision is on record yet. Ask the patient, or wait for the AI receptionist's record.", priority: 2 };
    case "CONSENT":
      return l.consent_frozen
        ? { cta: null, hint: "The patient declined or withdrew. Their details are not kept, so there is nothing to do unless they change their mind.", priority: 4 }
        : { cta: "Ask for consent", hint: "Personal details are not kept yet. Ask the patient whether they agree.", priority: 2 };
    case "QUALIFICATION":
      return { cta: "Review enquiry", hint: l.held_for_person ? "This enquiry is waiting for a person to review it. It was not qualified automatically." : "Check the enquiry is complete, then qualify it.", priority: l.held_for_person ? 1 : 2 };
    case "PROVIDER_OPTIONS":
      return { cta: "Choose a clinic", hint: "Qualified. Pick a verified clinic and ask the patient which they prefer.", priority: 2 };
    case "PATIENT_APPROVAL":
      return ref?.status === "APPROVED"
        ? { cta: "Send referral", hint: `The patient approved sharing with ${clinic}. Nothing has gone yet.`, priority: 1 }
        : { cta: "Record decision", hint: `Ask the patient whether they agree to share their details with ${clinic}, and record what they say.`, priority: 2 };
    case "REFERRED": {
      const d = daysSince(ref?.sent_at, now);
      return d !== null && d >= CHASE_AFTER_DAYS
        ? { cta: "Chase the clinic", hint: `${clinic} has not answered for ${d} days.`, priority: 2 }
        : { cta: null, hint: `Sent to ${clinic}. Waiting for their answer.`, priority: 4 };
    }
    case "CLINIC_RESPONSE":
      return ref?.status === "DECLINED"
        ? { cta: "Choose another clinic", hint: `${clinic} declined. The patient can be referred somewhere else if they agree.`, priority: 2 }
        : { cta: "Record the outcome", hint: `${clinic} accepted. Record what they report once the consultation has happened.`, priority: 3 };
    case "OUTCOME": {
      switch (ref?.commission_status ?? null) {
        case null:
          return { cta: "Set up the commission", hint: "The clinic reported an outcome. Record the commission you agreed; it counts as earned only once the agreed outcome is confirmed with evidence.", priority: 2 };
        case "PENDING":
          return { cta: "Check the commission", hint: "The commission is recorded but not earned yet. If the clinic has reported the outcome your agreement names, mark it earned.", priority: 3 };
        case "CONFIRMED":
          return { cta: "Invoice the clinic", hint: "The commission is earned. Send the clinic an invoice, then mark it invoiced.", priority: 2 };
        case "INVOICED":
          return { cta: "Check for payment", hint: "Invoiced. Mark it paid when the money arrives.", priority: 3 };
        case "DISPUTED":
          return { cta: "Resolve the dispute", hint: "The commission is disputed. Settle it, then return it to earned or invoiced.", priority: 2 };
        default:
          return { cta: null, hint: ref?.commission_status === "PAID" ? "Done. The commission was paid." : "Done. No commission will be earned.", priority: 4 };
      }
    }
  }
}

export interface NeedsItem {
  leadId: string;
  patient: string;
  /** What needs doing, as a sentence. */
  headline: string;
  /** Who and what, for context: treatment, destination, clinic. */
  context: string;
  cta: string;
  href: string;
  priority: 1 | 2 | 3;
  openTasks: number;
}

/** "Dental implants · Turkey" from whatever the patient's consent lets us show. */
export function contextLine(l: PipelineLead): string {
  const i = l.intake;
  return [i?.procedure, i?.destination].filter(Boolean).join(" · ");
}

const taskHeadline = (t: HandoffTask): string => {
  const kind = TASK_KIND_LABEL[t.kind] ?? "Needs a person";
  const why = t.reason_code ? TASK_REASON_LABEL[t.reason_code] : null;
  return why ? `${kind}: ${why}` : kind;
};

/**
 * One entry per patient who needs something from the owner, most urgent first. A patient's open task outranks their stage step; a patient with nothing to do
 * (waiting on a clinic, consent withdrawn) is not listed.
 */
export function buildNeedsYou(leads: PipelineLead[], tasks: HandoffTask[], now: number = Date.now()): NeedsItem[] {
  const openByLead = new Map<string, HandoffTask[]>();
  for (const t of tasks) {
    if (t.status !== "OPEN") continue;
    openByLead.set(t.lead_id, [...(openByLead.get(t.lead_id) ?? []), t]);
  }
  const items: NeedsItem[] = [];
  for (const l of leads) {
    const step = nextStepFor(l, now);
    const mine = openByLead.get(l.lead_id) ?? [];
    const urgentTask = mine.find((t) => t.kind === "escalation" || t.reason_code === "emergency" || t.reason_code === "consent_withdrawn_after_send");
    if (mine.length > 0) {
      const t = urgentTask ?? mine[0];
      items.push({
        leadId: l.lead_id, patient: patientName(l), headline: taskHeadline(t), context: contextLine(l), cta: "Open case", href: caseHref(l.lead_id),
        priority: urgentTask ? 1 : 2, openTasks: mine.length,
      });
      continue;
    }
    if (!step.cta) continue;
    items.push({
      leadId: l.lead_id, patient: patientName(l), headline: step.hint, context: contextLine(l), cta: step.cta, href: caseHref(l.lead_id),
      priority: Math.min(step.priority, 3) as 1 | 2 | 3, openTasks: 0,
    });
  }
  // Same urgency: a person who has asked for help (an open task) comes before a routine step.
  return items.sort((a, b) => a.priority - b.priority || Number(b.openTasks > 0) - Number(a.openTasks > 0));
}

/** Patients with a referral out and nothing for the owner to do yet: shown quietly as "waiting on others". */
export function waitingOnOthers(leads: PipelineLead[], now: number = Date.now()): { leadId: string; patient: string; clinic: string; sentAgo: string }[] {
  return leads
    .filter((l) => l.stage === "REFERRED" && nextStepFor(l, now).cta === null)
    .map((l) => {
      const ref = currentReferral(l);
      return { leadId: l.lead_id, patient: patientName(l), clinic: ref?.provider_name ?? "the clinic", sentAgo: ageLabel(ref?.sent_at, now) };
    });
}

/** A short, honest headline for the top of the Today screen. */
export function todaySummary(items: NeedsItem[]): string {
  if (items.length === 0) return "You're all caught up. Nothing needs you right now.";
  const urgent = items.filter((i) => i.priority === 1).length;
  const base = `${items.length} ${items.length === 1 ? "thing needs" : "things need"} you`;
  return urgent > 0 ? `${base}, ${urgent} ${urgent === 1 ? "is" : "are"} urgent.` : `${base}.`;
}
