import { describe, expect, it } from "vitest";
import { agoPhrase, clinicLane, crewHeadline, crewRows, journeySteps, todayLede } from "@/lib/mtToday";

const NOW = Date.parse("2026-10-27T12:00:00Z");
const hoursAgo = (h: number) => new Date(NOW - h * 3_600_000).toISOString();
const prov = (over: object) => ({ status: "ACTIVE", contact_email: null, verification_status: "UNDER_REVIEW", ...over });

describe("clinicLane", () => {
  it("counts only active clinics and says none can be referred to until one is verified", () => {
    const lane = clinicLane([prov({ contact_email: "a@x.com" }), prov({}), prov({ status: "INACTIVE" }), prov({ verification_status: "UNVERIFIED" })]);
    expect(lane).toMatchObject({ total: 3, withContact: 1, verified: 0, underReview: 3 });
    expect(lane.blocker).toMatch(/None of your 3 clinics is verified/);
  });
  it("has no blocker once a clinic is verified, and a different one when the directory is empty", () => {
    expect(clinicLane([prov({ verification_status: "VERIFIED" })]).blocker).toBe("");
    expect(clinicLane([]).blocker).toMatch(/No clinics are listed/);
  });
});

describe("agoPhrase", () => {
  it("speaks in hours, then days, and is empty for bad input", () => {
    expect(agoPhrase(hoursAgo(0), NOW)).toBe("just now");
    expect(agoPhrase(hoursAgo(1), NOW)).toBe("1 hour ago");
    expect(agoPhrase(hoursAgo(5), NOW)).toBe("5 hours ago");
    expect(agoPhrase(hoursAgo(30), NOW)).toBe("yesterday");
    expect(agoPhrase(hoursAgo(24 * 4), NOW)).toBe("4 days ago");
    expect(agoPhrase(null, NOW)).toBe("");
    expect(agoPhrase("nope", NOW)).toBe("");
  });
});

const member = (over: object) => ({ key: "k", name: "Alpha", purpose: "Does a thing. [operating crew]", trigger: "Every day at 08:00", status: "ACTIVE", running: true, agent_id: "A1", ...over });
const act = (key: string, over: object) => ({ key, name: key, agent_id: "A1", runs: 1, failed: 0, last: null, ...over });

describe("crewRows", () => {
  it("puts what is stuck first, then what ran, then what is idle, then what is not set up or off", () => {
    const crew = [
      member({ key: "idle", name: "Idle" }),
      member({ key: "ok", name: "Ok" }),
      member({ key: "bad", name: "Bad" }),
      member({ key: "off", name: "Off", switched_off: true }),
      member({ key: "new", name: "New", status: "NOT_SET_UP", running: false, agent_id: null }),
    ];
    const activity = [
      act("ok", { last: { status: "COMPLETED", at: hoursAgo(2), trigger: "SCHEDULE", summary: "Wrote the brief." } }),
      act("bad", { failed: 1, last: { status: "FAILED", at: hoursAgo(3), trigger: "EVENT", summary: null } }),
    ];
    const rows = crewRows(crew, activity, NOW);
    expect(rows.map((r) => r.key)).toEqual(["bad", "ok", "idle", "new", "off"]);
    expect(rows[0].line).toBe("Could not finish its last run (3 hours ago). Open it to see why.");
    expect(rows[1].line).toBe("2 hours ago: Wrote the brief.");
    expect(rows[2].line).toBe("Has not run in the last 7 days. It starts on every day at 08:00.");
  });
  it("never invents a result: a finished run without text says only when and what started it", () => {
    const rows = crewRows([member({ key: "ok" })], [act("ok", { last: { status: "COMPLETED", at: hoursAgo(1), trigger: "EVENT", summary: null } })], NOW);
    expect(rows[0].line).toBe("Finished its last run 1 hour ago, started by an event.");
  });
  it("still lists everyone when the activity could not be read", () => {
    const rows = crewRows([member({ key: "a" })], null, NOW);
    expect(rows).toHaveLength(1);
    expect(rows[0].state).toBe("waiting");
  });
  it("shows a run held for approval as needing the owner", () => {
    const rows = crewRows([member({ key: "a" })], [act("a", { last: { status: "WAITING_APPROVAL", at: hoursAgo(1), trigger: "EVENT", summary: null } })], NOW);
    expect(rows[0]).toMatchObject({ state: "stuck", line: "Waiting for your approval (1 hour ago)." });
  });
});

describe("crewHeadline", () => {
  it("summarises ran, stuck and idle, and ignores members that are off or not set up", () => {
    const rows = crewRows(
      [member({ key: "a" }), member({ key: "b" }), member({ key: "c" }), member({ key: "d", switched_off: true })],
      [act("a", { last: { status: "COMPLETED", at: hoursAgo(1), trigger: "EVENT", summary: null } }), act("b", { last: { status: "FAILED", at: hoursAgo(1), trigger: "EVENT", summary: null } })],
      NOW,
    );
    expect(crewHeadline(rows)).toBe("1 of 3 ran in the last 7 days, 1 could not finish, 1 had nothing to react to.");
    expect(crewHeadline([])).toBe("No AI crew members are running.");
  });
});

describe("journeySteps", () => {
  const lane = clinicLane([prov({}), prov({})]);
  it("marks the first empty step as where the business is stuck", () => {
    const steps = journeySteps({ clinics: lane, received: 4, qualified: 2, stageCounts: { REFERRED: 1 }, earned: 0 });
    expect(steps.map((s) => s.count)).toEqual([2, 0, 4, 2, 1, 0]);
    expect(steps.filter((s) => s.here).map((s) => s.key)).toEqual(["verified"]);
  });
  it("marks nothing when every step has something, and counts a referral that moved on as referred", () => {
    const steps = journeySteps({ clinics: clinicLane([prov({ verification_status: "VERIFIED" })]), received: 1, qualified: 1, stageCounts: { CLINIC_RESPONSE: 1, OUTCOME: 2 }, earned: 1 });
    expect(steps.some((s) => s.here)).toBe(false);
    expect(steps.find((s) => s.key === "referred")?.count).toBe(3);
  });
  it("treats unknown numbers as empty rather than guessing", () => {
    const steps = journeySteps({ clinics: null, received: null, qualified: null, stageCounts: {}, earned: 0 });
    expect(steps.every((s) => s.count === 0)).toBe(true);
    expect(steps[0].here).toBe(true);
  });
});

describe("todayLede", () => {
  it("keeps the patient summary when nothing else is waiting", () => {
    expect(todayLede("You're all caught up. Nothing needs you right now.", 0, 0)).toBe("You're all caught up. Nothing needs you right now.");
  });
  it("never says all caught up while approvals or other asks are waiting", () => {
    expect(todayLede("You're all caught up. Nothing needs you right now.", 0, 3)).toBe("No patient needs you right now, but 3 other things are waiting for you below.");
    expect(todayLede("1 thing needs you.", 1, 1)).toBe("1 thing needs you. Also, 1 other thing is waiting for you below.");
  });
});
