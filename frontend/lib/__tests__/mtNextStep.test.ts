import { describe, expect, it } from "vitest";
import { pageNextStep } from "@/lib/mtNextStep";

describe("pageNextStep", () => {
  it("names the first blocker on money pages and points at the clinics", () => {
    const s = pageNextStep("/medical-tourism/revenue", { total: 37, withContact: 9, verified: 0 })!;
    expect(s.body).toMatch(/None of your 37 clinics is verified yet \(9 have an email on file\)/);
    expect(s.action).toEqual({ label: "Open clinics", href: "/medical-tourism/providers" });
  });
  it("moves on to patients once a clinic is verified", () => {
    const s = pageNextStep("/finance/invoices", { total: 5, withContact: 5, verified: 2 })!;
    expect(s.title).toBe("Clinic invoices are created for you");
    expect(s.action.href).toBe("/medical-tourism/pipeline");
  });
  it("says so when no clinic is listed, and never claims numbers it does not have", () => {
    expect(pageNextStep("/medical-tourism/referrals", { total: 0, withContact: 0, verified: 0 })!.action.label).toBe("Add a clinic");
    const unknown = pageNextStep("/marketing", null)!;
    expect(unknown.body).not.toMatch(/\d+ of \d+/);
    expect(unknown.action.href).toBe("/marketing/campaigns");
  });
  it("tells the owner to record which clinic offers which procedure, without assuming the list is empty", () => {
    const s = pageNextStep("/medical-tourism/procedures", null)!;
    expect(s.title).toBe("A clinic is matched to a patient through the procedures it offers");
    expect(s.body).not.toMatch(/list each treatment/i);
    expect(s.body).toMatch(/record which clinic performs which procedure/);
  });
  it("shows nothing on pages that have their own content", () => {
    expect(pageNextStep("/leads", null)).toBeNull();
    expect(pageNextStep("/medical-tourism/pipeline", null)).toBeNull();
  });
});
