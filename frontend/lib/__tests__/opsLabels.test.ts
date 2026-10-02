import { describe, expect, it } from "vitest";
import { actionLabel, leadStatusLabel, sourceLabel, titleCase, triggerLabel, when } from "@/lib/opsLabels";

describe("opsLabels — never show engine identifiers", () => {
  it("humanises known and unknown actions", () => {
    expect(actionLabel("notifications.create_notification")).toBe("Notify the team");
    expect(actionLabel("quotes.detect_expired_quotes")).toBe("Detect expired quotes");
  });
  it("humanises triggers, including manual", () => {
    expect(triggerLabel("lead.created")).toBe("A new lead arrives");
    expect(triggerLabel(null)).toBe("Started manually");
    expect(triggerLabel("thing.happened")).toBe("When “Thing happened” happens");
  });
  it("labels statuses and sources without leaking raw codes", () => {
    expect(leadStatusLabel("UNQUALIFIED")).toBe("Not a fit");
    expect(leadStatusLabel("WEIRD_STATE")).toBe("Weird state");
    expect(sourceLabel("WEB")).toBe("Website");
  });
  it("handles empty/invalid input safely", () => {
    expect(titleCase("")).toBe("");
    expect(when(null)).toBe("");
    expect(when("not-a-date")).toBe("");
    expect(when(new Date().toISOString())).toBe("just now");
  });
});
