/**
 * Today page helpers: where the business is blocked on the clinic side, and what each AI crew member did. Pure functions over facts the backend already
 * derived; they decide nothing and invent nothing, they only turn counts and times into plain sentences.
 */
import type { CrewActivityMember, MedicalTourismProvider, OperatingCrewMember } from "@/lib/api";

export type ClinicLane = {
  total: number;
  withContact: number;
  underReview: number;
  verified: number;
  /** The one sentence that says what is stopping the first referral, or empty when nothing is. */
  blocker: string;
};

/** Counts the clinic directory the way a referral needs it: only an active clinic that is verified can receive a patient. */
export function clinicLane(providers: Pick<MedicalTourismProvider, "status" | "contact_email" | "verification_status">[]): ClinicLane {
  const active = providers.filter((p) => p.status === "ACTIVE");
  const verified = active.filter((p) => p.verification_status === "VERIFIED").length;
  const withContact = active.filter((p) => !!p.contact_email).length;
  const underReview = active.filter((p) => p.verification_status !== "VERIFIED").length;
  let blocker = "";
  if (active.length === 0) blocker = "No clinics are listed yet, so no patient can be matched.";
  else if (verified === 0) blocker = `None of your ${active.length} clinics is verified yet, so no patient can be referred to one. Verifying a clinic is the first thing to do.`;
  return { total: active.length, withContact, underReview, verified, blocker };
}

const HOUR = 60 * 60 * 1000;

/** "just now", "3 hours ago", "yesterday", "4 days ago". Empty for a missing or unreadable time. */
export function agoPhrase(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const hours = Math.floor((now - t) / HOUR);
  if (hours < 1) return "just now";
  if (hours < 24) return hours === 1 ? "1 hour ago" : `${hours} hours ago`;
  const days = Math.floor(hours / 24);
  return days === 1 ? "yesterday" : `${days} days ago`;
}

export type CrewRow = {
  key: string;
  name: string;
  purpose: string;
  trigger: string;
  agentId: string | null;
  state: "done" | "stuck" | "waiting" | "off" | "not_set_up";
  /** One plain sentence about the last run, or why there is none. */
  line: string;
  runs: number;
  failed: number;
};

const TRIGGER_WORD: Record<string, string> = { EVENT: "an event", SCHEDULE: "its schedule", MANUAL: "a manual run" };

/** Joins the crew list with its recent activity into one row per member, ordered so what needs a look comes first. */
export function crewRows(crew: OperatingCrewMember[], activity: CrewActivityMember[] | null, now: number = Date.now()): CrewRow[] {
  const byKey = new Map((activity ?? []).map((a) => [a.key, a]));
  const rows = crew.map((m): CrewRow => {
    const a = byKey.get(m.key);
    const base = { key: m.key, name: m.name, purpose: m.purpose, trigger: m.trigger, agentId: m.agent_id, runs: a?.runs ?? 0, failed: a?.failed ?? 0 };
    if (m.status === "NOT_SET_UP") return { ...base, state: "not_set_up", line: "Not set up yet." };
    if (m.switched_off) return { ...base, state: "off", line: "Switched off." };
    const last = a?.last;
    if (!last) return { ...base, state: "waiting", line: `Has not run in the last 7 days. It starts on ${m.trigger.toLowerCase()}.` };
    const when = agoPhrase(last.at, now);
    if (last.status === "FAILED") return { ...base, state: "stuck", line: `Could not finish its last run (${when}). Open it to see why.` };
    if (last.status === "WAITING_APPROVAL") return { ...base, state: "stuck", line: `Waiting for your approval (${when}).` };
    if (last.status === "COMPLETED") return { ...base, state: "done", line: last.summary ? `${when}: ${last.summary}` : `Finished its last run ${when}, started by ${TRIGGER_WORD[last.trigger] ?? "a trigger"}.` };
    return { ...base, state: "done", line: `Running now or just started (${when}).` };
  });
  const order = { stuck: 0, done: 1, waiting: 2, not_set_up: 3, off: 4 } as const;
  return rows.sort((x, y) => order[x.state] - order[y.state] || x.name.localeCompare(y.name));
}

/** The headline for the AI section: how many did something, how many are stuck, how many simply had nothing to do. */
export function crewHeadline(rows: CrewRow[]): string {
  const active = rows.filter((r) => r.state !== "off" && r.state !== "not_set_up");
  if (active.length === 0) return "No AI crew members are running.";
  const done = active.filter((r) => r.state === "done").length;
  const stuck = active.filter((r) => r.state === "stuck").length;
  const waiting = active.filter((r) => r.state === "waiting").length;
  const parts = [`${done} of ${active.length} ran in the last 7 days`];
  if (stuck > 0) parts.push(`${stuck} could not finish`);
  if (waiting > 0) parts.push(`${waiting} had nothing to react to`);
  return `${parts.join(", ")}.`;
}

export type JourneyStep = { key: string; label: string; count: number; hint: string; href: string; here: boolean };

type JourneyInput = {
  clinics: ClinicLane | null;
  received: number | null;
  qualified: number | null;
  stageCounts: Record<string, number>;
  /** How many commissions are earned or later (confirmed, invoiced or paid), across currencies. */
  earned: number;
};

/**
 * The whole path from "we have clinics" to "we earned a commission", in the order it has to happen. `here` marks the first step with nothing in it:
 * that is where the business is stuck, so it is where the owner's attention should go. A step whose number is unknown is shown as 0 and never marked.
 */
export function journeySteps(i: JourneyInput): JourneyStep[] {
  const sent = (i.stageCounts.REFERRED ?? 0) + (i.stageCounts.CLINIC_RESPONSE ?? 0) + (i.stageCounts.OUTCOME ?? 0);
  const steps: Omit<JourneyStep, "here">[] = [
    { key: "clinics", label: "Clinics listed", count: i.clinics?.total ?? 0, hint: "Hospitals you could refer patients to", href: "/medical-tourism/providers" },
    { key: "verified", label: "Clinics verified", count: i.clinics?.verified ?? 0, hint: "Only a verified clinic can receive a patient", href: "/medical-tourism/providers" },
    { key: "enquiries", label: "Patient enquiries", count: i.received ?? 0, hint: "From your website, calls and messages", href: "/medical-tourism/pipeline" },
    { key: "qualified", label: "Qualified", count: i.qualified ?? 0, hint: "Checked, with consent on record", href: "/medical-tourism/pipeline" },
    { key: "referred", label: "Referred to a clinic", count: sent, hint: "Sent with the patient's approval", href: "/medical-tourism/provider-referrals" },
    { key: "earned", label: "Commission earned", count: i.earned, hint: "Confirmed with the clinic's evidence", href: "/medical-tourism/revenue" },
  ];
  const firstEmpty = steps.findIndex((s) => s.count === 0);
  return steps.map((s, idx) => ({ ...s, here: idx === firstEmpty }));
}
