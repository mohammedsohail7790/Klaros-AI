import { describe, expect, it } from "vitest";
import { column, countries, pipelineSummary, withGroups } from "@/lib/mtPipeline";

const terms = { commission_basis: null } as never;
const row = (name: string, stage: string | undefined, country = "Türkiye", extra: object = {}) => ({ provider_id: name, name, country, verification_status: "UNDER_REVIEW", referrals_sent: 0, terms, stage, next_step: "Do the next thing.", ...extra }) as never;

describe("withGroups", () => {
  it("tags clinics that share a first name, so a group with one contact is easy to see", () => {
    const rows = withGroups([row("Acibadem Atakent", "needs_contact"), row("Acibadem Maslak", "ready_to_invite"), row("Memorial Hospital", "needs_contact"), row("Liv Hospitals", "needs_contact")]);
    expect(rows.find((r) => r.name === "Acibadem Maslak")?.group).toBe("Acibadem");
    expect(rows.find((r) => r.name === "Liv Hospitals")?.group).toBeNull();
  });
  it("leaves out a clinic the backend gave no stage, rather than guessing one", () => {
    expect(withGroups([row("No Stage", undefined), row("Has Stage", "live")]).map((r) => r.name)).toEqual(["Has Stage"]);
  });
});

describe("column and countries", () => {
  const rows = withGroups([row("B Clinic", "needs_contact", "India"), row("A Clinic", "needs_contact", "Türkiye"), row("C Clinic", "live", "Thailand")]);
  it("lists one stage in name order and filters by country", () => {
    expect(column(rows, "needs_contact", "all").map((r) => r.name)).toEqual(["A Clinic", "B Clinic"]);
    expect(column(rows, "needs_contact", "India").map((r) => r.name)).toEqual(["B Clinic"]);
    expect(column(rows, "awaiting_reply", "all")).toEqual([]);
  });
  it("lists each country once, sorted", () => {
    expect(countries(rows)).toEqual(["India", "Thailand", "Türkiye"]);
  });
});

describe("pipelineSummary", () => {
  it("says nothing can be referred while no hospital is ready, and counts what is blocking", () => {
    const s = pipelineSummary(withGroups([row("A", "ready_to_invite"), row("B", "ready_to_invite"), row("C", "awaiting_reply"), row("D", "needs_contact")]));
    expect(s.headline).toBe("Nothing can be referred yet: no hospital is ready to receive a patient.");
    expect(s.detail).toBe("2 have a contact and are ready to be contacted, 1 is waiting for a reply, and 1 still needs a contact.");
  });
  it("changes its tune once a hospital is ready, and handles an empty list", () => {
    expect(pipelineSummary(withGroups([row("A", "live"), row("B", "needs_verification"), row("C", "needs_terms")])).headline).toBe("1 hospital is ready to receive a patient.");
    expect(pipelineSummary([]).headline).toBe("No hospitals are listed yet.");
  });
});
