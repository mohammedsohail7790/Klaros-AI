import { describe, expect, it } from "vitest";
import { getJourneyDestination, getJourneyStageLabel, isJourneyAtStage } from "@/lib/businessJourneyController";
import type { BusinessJourneyStatus } from "@/lib/api";

describe("businessJourneyController — the single frontend status->route mapping", () => {
  it.each([
    ["DISCOVERY_ACTIVE", "/business/discovery"],
    ["BLUEPRINT_REVIEW", "/business/blueprint"],
    ["BLUEPRINT_ACTIVE", "/business/requirements"],
    ["RECOMMENDATIONS_READY", "/business/recommendations"],
    ["COMPLETED", "/business/home"],
    ["ABANDONED", "/business"],
  ] as [BusinessJourneyStatus, string][])("routes %s to %s", (status, route) => {
    expect(getJourneyDestination(status)).toBe(route);
  });

  it("gives a truthful, non-empty stage label for every real status", () => {
    const statuses: BusinessJourneyStatus[] = [
      "DISCOVERY_ACTIVE",
      "BLUEPRINT_REVIEW",
      "BLUEPRINT_ACTIVE",
      "RECOMMENDATIONS_READY",
      "COMPLETED",
      "ABANDONED",
    ];
    for (const s of statuses) {
      expect(getJourneyStageLabel(s).length).toBeGreaterThan(0);
    }
  });

  it("isJourneyAtStage only allows the exact statuses passed in", () => {
    expect(isJourneyAtStage("DISCOVERY_ACTIVE", ["DISCOVERY_ACTIVE"])).toBe(true);
    expect(isJourneyAtStage("BLUEPRINT_REVIEW", ["DISCOVERY_ACTIVE"])).toBe(false);
    expect(isJourneyAtStage("RECOMMENDATIONS_READY", ["RECOMMENDATIONS_READY", "BLUEPRINT_ACTIVE"])).toBe(true);
  });
});
