import { describe, expect, it } from "vitest";
import { friendlyCapability, hasLine, toggleLine, SECTION_PROMPTS, CAPABILITY_OPTIONS } from "../blueprintPrompts";

describe("blueprintPrompts", () => {
  it("shows a raw capability key as plain words", () => {
    expect(friendlyCapability("medical_tourism.provider_directory")).toBe("Provider directory");
    expect(friendlyCapability("Website")).toBe("Website");
    expect(friendlyCapability("lead_capture")).toBe("Lead capture");
  });

  it("ticks and unticks a line, tolerating commas and different case", () => {
    expect(hasLine("Website, payments", "Payments")).toBe(true);
    expect(toggleLine("Website\nPayments", "Payments", false)).toBe("Website");
    expect(toggleLine("Website, Payments", "payments", false)).toBe("Website");
    expect(toggleLine("Website", "Payments", true)).toBe("Website\nPayments");
    expect(toggleLine("Website", "website", true)).toBe("Website");
  });

  it("has a plain question and example for every section that can be Missing", () => {
    for (const key of ["REVENUE", "CHANNELS", "MARKETING", "FINANCE", "OPERATIONS", "COMMUNICATIONS", "SUPPLIERS_PROVIDERS", "COMPLIANCE", "GOALS", "CONSTRAINTS", "ASSUMPTIONS", "DECISIONS"]) {
      expect(SECTION_PROMPTS[key].question.endsWith("?")).toBe(true);
      expect(SECTION_PROMPTS[key].placeholder.startsWith("For example")).toBe(true);
    }
    expect(new Set(CAPABILITY_OPTIONS).size).toBe(CAPABILITY_OPTIONS.length);
  });
});
