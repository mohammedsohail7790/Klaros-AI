import { describe, expect, it } from "vitest";
import { dedupeRecommendations } from "../recommendationDedupe";
import type { Recommendation } from "../api";

function rec(over: Partial<Recommendation>): Recommendation {
  return {
    id: "r", run_id: "run1", blueprint_id: "b", blueprint_version: 1, type: "INTEGRATION",
    capability_key: "lead_capture", provider_key: "google_ads", provider_implementation_status: "STUB",
    tool_name: null, what: "", why: "", based_on: null, dependencies: null, cost_estimate: null,
    required: false, alternatives: null, confidence: null, source: "x", source_vertical_key: null,
    status: "PROPOSED", decided_by: null, decided_at: null, rejection_reason: null,
    created_at: "2026-10-13T10:00:00Z", updated_at: "2026-10-13T10:00:00Z", ...over,
  };
}

describe("dedupeRecommendations", () => {
  it("collapses the same recommendation produced by two runs, keeping the newest", () => {
    const out = dedupeRecommendations([
      rec({ id: "old", run_id: "run1", created_at: "2026-10-13T10:00:00Z" }),
      rec({ id: "new", run_id: "run2", created_at: "2026-10-13T10:05:00Z" }),
    ]);
    expect(out.map((r) => r.id)).toEqual(["new"]);
  });

  it("prefers the row the owner already decided on over an undecided duplicate", () => {
    const out = dedupeRecommendations([
      rec({ id: "accepted", status: "ACCEPTED", created_at: "2026-10-13T10:00:00Z" }),
      rec({ id: "fresh", status: "PROPOSED", created_at: "2026-10-13T10:05:00Z" }),
    ]);
    expect(out.map((r) => r.id)).toEqual(["accepted"]);
  });

  it("within one requirement, the same provider tagged with different capability keys is one suggestion", () => {
    const rows = [
      rec({ id: "a", capability_key: "lead_capture", status: "ACCEPTED" }),
      rec({ id: "b", capability_key: "lead_generation", status: "REJECTED", created_at: "2026-10-13T10:05:00Z" }),
    ];
    expect(dedupeRecommendations(rows)).toHaveLength(2); // different capability keys: kept apart globally
    expect(dedupeRecommendations(rows, true).map((r) => r.id)).toEqual(["b"]); // same group: collapsed, newest decided wins
  });

  it("keeps different providers and drops superseded rows", () => {
    const out = dedupeRecommendations([
      rec({ id: "g", provider_key: "google_ads" }),
      rec({ id: "m", provider_key: "meta_ads" }),
      rec({ id: "s", provider_key: "xero", status: "SUPERSEDED" }),
    ]);
    expect(out.map((r) => r.id)).toEqual(["g", "m"]);
  });
});
