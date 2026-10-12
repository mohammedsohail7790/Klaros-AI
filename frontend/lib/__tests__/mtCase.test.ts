import { describe, expect, it } from "vitest";
import type { HandoffTask, PipelineLead } from "@/lib/api";
import { ageLabel, buildNeedsYou, nextStepFor, patientName, todaySummary, waitingOnOthers } from "@/lib/mtCase";

const NOW = Date.parse("2026-10-12T12:00:00Z");
const daysAgo = (n: number) => new Date(NOW - n * 86_400_000).toISOString();

const lead = (over: Partial<PipelineLead> = {}): PipelineLead => ({
  lead_id: "L1", name: "Layla Test", stage: "QUALIFICATION", stage_number: 3, next_action: "", held_for_person: false, consent_frozen: false,
  intake: { procedure: "Dental implants", destination: "Turkey", timeframe: null, language: null, contact_time: null },
  referrals: [], open_tasks: 0, assigned_user_id: null, created_at: daysAgo(2), ...over,
});
const ref = (status: string, over: object = {}) => ({ id: "R1", provider_id: "P1", provider_name: "Anatolia Dental", status, sent_at: null, clinic_response_at: null, outcome_reported_at: null, ...over });
const task = (over: Partial<HandoffTask> = {}): HandoffTask => ({ id: "T1", lead_id: "L1", kind: "escalation", reason_code: "clinical_question", status: "OPEN", owner_user_id: null, accepted_at: null, completed_at: null, note: null, created_at: daysAgo(1), ...over });

describe("ageLabel", () => {
  it("speaks in days and weeks, and is empty for a missing or unreadable time", () => {
    expect(ageLabel(daysAgo(0), NOW)).toBe("today");
    expect(ageLabel(daysAgo(1), NOW)).toBe("yesterday");
    expect(ageLabel(daysAgo(5), NOW)).toBe("5 days ago");
    expect(ageLabel(daysAgo(21), NOW)).toBe("3 weeks ago");
    expect(ageLabel(null, NOW)).toBe("");
    expect(ageLabel("not a date", NOW)).toBe("");
  });
});

describe("nextStepFor", () => {
  it("gives every stage a plain next step, and none to a patient who withdrew", () => {
    expect(nextStepFor(lead({ stage: "PROVIDER_OPTIONS" }), NOW).cta).toBe("Choose a clinic");
    expect(nextStepFor(lead({ stage: "CONSENT", consent_frozen: true, name: null }), NOW)).toMatchObject({ cta: null, priority: 4 });
    expect(nextStepFor(lead({ stage: "CONSENT" }), NOW).cta).toBe("Ask for consent");
  });

  it("an approved referral that has not been sent is urgent; a draft is the approval conversation", () => {
    expect(nextStepFor(lead({ stage: "PATIENT_APPROVAL", referrals: [ref("APPROVED")] }), NOW)).toMatchObject({ cta: "Send referral", priority: 1 });
    expect(nextStepFor(lead({ stage: "PATIENT_APPROVAL", referrals: [ref("DRAFT")] }), NOW).cta).toBe("Record decision");
  });

  it("only asks the owner to chase a clinic after a few quiet days", () => {
    expect(nextStepFor(lead({ stage: "REFERRED", referrals: [ref("SENT", { sent_at: daysAgo(1) })] }), NOW).cta).toBeNull();
    expect(nextStepFor(lead({ stage: "REFERRED", referrals: [ref("SENT", { sent_at: daysAgo(4) })] }), NOW)).toMatchObject({ cta: "Chase the clinic", priority: 2 });
  });

  it("a declined referral means choose another clinic; an accepted one means record the outcome", () => {
    expect(nextStepFor(lead({ stage: "CLINIC_RESPONSE", referrals: [ref("DECLINED")] }), NOW).cta).toBe("Choose another clinic");
    expect(nextStepFor(lead({ stage: "CLINIC_RESPONSE", referrals: [ref("ACCEPTED")] }), NOW).cta).toBe("Record the outcome");
  });

  it("a cancelled referral is ignored when naming the clinic", () => {
    const l = lead({ stage: "PATIENT_APPROVAL", referrals: [ref("CANCELLED", { provider_name: "Old Clinic" }), ref("DRAFT", { id: "R2", provider_name: "New Clinic" })] });
    expect(nextStepFor(l, NOW).hint).toContain("New Clinic");
  });
});

describe("buildNeedsYou", () => {
  it("lists only people who need something, most urgent first, one entry per patient", () => {
    const leads = [
      lead({ lead_id: "A", name: "Calm", stage: "REFERRED", referrals: [ref("SENT", { sent_at: daysAgo(1) })] }), // waiting: not listed
      lead({ lead_id: "B", name: "Needs Clinic", stage: "PROVIDER_OPTIONS" }),
      lead({ lead_id: "C", name: "Ready To Send", stage: "PATIENT_APPROVAL", referrals: [ref("APPROVED")] }),
      lead({ lead_id: "D", name: "Escalated", stage: "QUALIFICATION" }),
    ];
    const items = buildNeedsYou(leads, [task({ lead_id: "D" }), task({ id: "T2", lead_id: "D", kind: "halla_handoff", reason_code: null })], NOW);
    expect(items.map((i) => i.patient)).toEqual(["Escalated", "Ready To Send", "Needs Clinic"]);
    expect(items[0]).toMatchObject({ priority: 1, openTasks: 2, cta: "Open case", href: "/medical-tourism/patients/D" });
    expect(items[0].headline).toContain("Needs a person");
    expect(items[0].headline).toContain("medical question");
  });

  it("names a patient whose details are withheld without inventing anything, and shows context only when consent allowed it", () => {
    const l = lead({ name: null, intake: { procedure: null, destination: null, timeframe: null, language: null, contact_time: null }, stage: "QUALIFICATION" });
    expect(patientName(l)).toBe("Patient (details withheld)");
    const [item] = buildNeedsYou([l], [], NOW);
    expect(item.patient).toBe("Patient (details withheld)");
    expect(item.context).toBe("");
  });

  it("an accepted (owned) task no longer shouts", () => {
    expect(buildNeedsYou([lead({ stage: "REFERRED", referrals: [ref("SENT", { sent_at: daysAgo(0) })] })], [task({ status: "ACCEPTED" })], NOW)).toEqual([]);
  });
});

describe("todaySummary and waiting", () => {
  it("says honestly when there is nothing to do and counts what is urgent", () => {
    expect(todaySummary([])).toMatch(/all caught up/);
    const items = buildNeedsYou([lead({ lead_id: "C", stage: "PATIENT_APPROVAL", referrals: [ref("APPROVED")] }), lead({ lead_id: "B", stage: "PROVIDER_OPTIONS" })], [], NOW);
    expect(todaySummary(items)).toBe("2 things need you, 1 is urgent.");
  });

  it("lists patients waiting on a clinic quietly, with how long", () => {
    expect(waitingOnOthers([lead({ stage: "REFERRED", referrals: [ref("SENT", { sent_at: daysAgo(2) })] })], NOW)).toEqual([{ leadId: "L1", patient: "Layla Test", clinic: "Anatolia Dental", sentAgo: "2 days ago" }]);
  });
});

import { countryName, referableNote } from "@/lib/mtLabels";

describe("clinic labels", () => {
  it("shows a country by name, and leaves anything else as given", () => {
    expect(countryName("TR")).toMatch(/T(ü|u)rkiye|Turkey/);
    expect(countryName("in")).toBe("India");
    expect(countryName("Atlantis")).toBe("Atlantis");
    expect(countryName(null)).toBe("");
  });

  it("says whether a clinic can receive referrals and what to do when it cannot", () => {
    expect(referableNote("ACTIVE", "VERIFIED")).toEqual({ ok: true, text: "Can receive referrals" });
    expect(referableNote("ACTIVE", "EXPIRED").text).toMatch(/renew/);
    expect(referableNote("ACTIVE", undefined).ok).toBe(false);
    expect(referableNote("INACTIVE", "VERIFIED").ok).toBe(false);
  });
});
