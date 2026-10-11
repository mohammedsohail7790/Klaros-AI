/**
 * Phase 14: the ONE frontend journey-state mapping layer.
 *
 * This module contains presentation logic only — it maps the backend's
 * authoritative `BusinessJourney.status` (Phase 13) to a route and a
 * human-readable stage label. It never decides whether a transition is
 * legal (that's BusinessJourneyService server-side), never duplicates
 * Discovery-completion/Blueprint-minimum-bar/Recommendation-matching
 * rules, and never invents a status the backend didn't return. Every
 * page in frontend/app/business/** imports from here instead of
 * re-deriving its own copy of this switch, so there is exactly one place
 * that can disagree with the backend.
 */

import type { BusinessJourneyStatus } from "./api";

export type JourneyRoute =
  | "/business"
  | "/business/discovery"
  | "/business/blueprint"
  | "/business/requirements"
  | "/business/recommendations"
  | "/business/home";

/** Where a user with a journey in this status should be looking. */
export function getJourneyDestination(status: BusinessJourneyStatus): JourneyRoute {
  switch (status) {
    case "DISCOVERY_ACTIVE":
      return "/business/discovery";
    case "BLUEPRINT_REVIEW":
      return "/business/blueprint";
    case "BLUEPRINT_ACTIVE":
      // A confirmed Blueprint continues into Requirements, where the user
      // generates recommendations; the Blueprint page itself stays
      // reachable (it renders in both statuses).
      return "/business/requirements";
    case "RECOMMENDATIONS_READY":
      return "/business/recommendations";
    case "COMPLETED":
      return "/business/home";
    case "ABANDONED":
      return "/business";
    default:
      return "/business";
  }
}

/** Truthful, non-fabricated stage copy — no invented percentages or
 * question counts beyond what the backend actually reports elsewhere. */
export function getJourneyStageLabel(status: BusinessJourneyStatus): string {
  switch (status) {
    case "DISCOVERY_ACTIVE":
      return "Discovery in progress";
    case "BLUEPRINT_REVIEW":
      return "Blueprint ready for review";
    case "BLUEPRINT_ACTIVE":
      return "Blueprint confirmed";
    case "RECOMMENDATIONS_READY":
      return "Recommendations ready";
    case "COMPLETED":
      return "Business set up";
    case "ABANDONED":
      return "Journey abandoned";
    default:
      return "";
  }
}

/** A page for stage X calls this with its OWN expected status set; if the
 * journey isn't in one of them, the page redirects to the authoritative
 * destination instead of rendering — this is what prevents a direct-URL
 * visit from bypassing a checkpoint (e.g. typing /business/recommendations
 * while still in DISCOVERY_ACTIVE). */
export function isJourneyAtStage(status: BusinessJourneyStatus, allowed: BusinessJourneyStatus[]): boolean {
  return allowed.includes(status);
}
