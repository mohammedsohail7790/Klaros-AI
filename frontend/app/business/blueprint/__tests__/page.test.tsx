import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const pushMock = vi.fn();
const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: replaceMock }),
  usePathname: () => "/business/blueprint",
}));

const getCurrentBusinessJourneyMock = vi.fn();
const listBusinessJourneysMock = vi.fn().mockResolvedValue([]);
const getDraftBlueprintMock = vi.fn();
const getActiveBlueprintMock = vi.fn();
const confirmBlueprintClaimMock = vi.fn();
const rejectBlueprintClaimMock = vi.fn();
const confirmBlueprintJourneyStepMock = vi.fn();
const generateRecommendationsJourneyStepMock = vi.fn();
const updateBlueprintSectionMock = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  BLUEPRINT_SECTION_LABELS: {
    IDENTITY: "Business Identity",
    REQUIRED_CAPABILITIES: "Required Capabilities",
  },
  getCurrentBusinessJourney: (...args: unknown[]) => getCurrentBusinessJourneyMock(...args),
  listBusinessJourneys: (...args: unknown[]) => listBusinessJourneysMock(...args),
  getDraftBlueprint: (...args: unknown[]) => getDraftBlueprintMock(...args),
  getActiveBlueprint: (...args: unknown[]) => getActiveBlueprintMock(...args),
  confirmBlueprintClaim: (...args: unknown[]) => confirmBlueprintClaimMock(...args),
  rejectBlueprintClaim: (...args: unknown[]) => rejectBlueprintClaimMock(...args),
  confirmBlueprintJourneyStep: (...args: unknown[]) => confirmBlueprintJourneyStepMock(...args),
  generateRecommendationsJourneyStep: (...args: unknown[]) => generateRecommendationsJourneyStepMock(...args),
  updateBlueprintSection: (...args: unknown[]) => updateBlueprintSectionMock(...args),
  listNotifications: vi.fn().mockResolvedValue({ notifications: [] }),
  getUnreadNotificationCount: vi.fn().mockResolvedValue({ count: 0 }),
  markNotificationRead: vi.fn(),
  markAllNotificationsRead: vi.fn(),
  dismissNotification: vi.fn(),
  logout: vi.fn(),
}));

vi.mock("@/lib/useAuth", () => ({
  useAuth: () => ({
    token: "test-token",
    user: { id: "u1", tenant_id: "t1", email: "owner@example.com", full_name: "Test Owner", role: "OWNER" },
    loading: false,
    error: null,
  }),
}));

import BlueprintPage from "@/app/business/blueprint/page";

function journey(status: string) {
  return {
    id: "j1",
    status,
    discovery_session_id: "d1",
    blueprint_id: "b1",
    recommendation_run_id: null,
    created_by: "u1",
    completed_at: null,
    abandoned_at: null,
    last_error: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

function draftBlueprintResponse() {
  return {
    blueprint: { id: "b1", status: "DRAFT", version: 1, created_by: "u1", confirmed_at: null, vertical_extension_id: null, supersedes_id: null, created_at: "x", updated_at: "x" },
    sections: [
      { id: "s1", section_key: "IDENTITY", status: "COMPLETE", data: { name: "Acme Bakery" }, updated_by: "u1", updated_at: "x" },
      { id: "s2", section_key: "REQUIRED_CAPABILITIES", status: "EMPTY", data: {}, updated_by: null, updated_at: "x" },
    ],
    claims: [
      { id: "c1", blueprint_id: "b1", section_key: "IDENTITY", claim_type: "Fact", key: "tagline", value: "Fresh bread daily", confidence: 0.9, provenance: "USER_STATED", discovery_turn_id: null, evidence_ref: null, status: "PROPOSED", confirmed_by: null, confirmed_at: null, rejected_reason: null },
    ],
  };
}

describe("Blueprint page", () => {
  beforeEach(() => {
    getCurrentBusinessJourneyMock.mockReset();
    getDraftBlueprintMock.mockReset();
    getActiveBlueprintMock.mockReset();
    confirmBlueprintClaimMock.mockReset();
    rejectBlueprintClaimMock.mockReset();
    confirmBlueprintJourneyStepMock.mockReset();
    generateRecommendationsJourneyStepMock.mockReset();
    replaceMock.mockReset();
  });

  it("renders actual sections and their data from the backend", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("BLUEPRINT_REVIEW"));
    getDraftBlueprintMock.mockResolvedValue(draftBlueprintResponse());

    render(<BlueprintPage />);
    expect(await screen.findByText("Business Identity")).toBeInTheDocument();
    expect(screen.getByText("Acme Bakery")).toBeInTheDocument();
    expect(screen.getByText("Required Capabilities")).toBeInTheDocument();
  });

  it("shows empty-section state without fabricating content", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("BLUEPRINT_REVIEW"));
    getDraftBlueprintMock.mockResolvedValue(draftBlueprintResponse());
    render(<BlueprintPage />);
    expect(await screen.findByText(/Klaros doesn't know this yet/)).toBeInTheDocument();
  });

  it("confirms a proposed claim through the backend and reconciles", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("BLUEPRINT_REVIEW"));
    getDraftBlueprintMock.mockResolvedValue(draftBlueprintResponse());
    confirmBlueprintClaimMock.mockResolvedValue({});
    render(<BlueprintPage />);
    fireEvent.click(await screen.findByText("Yes"));
    await waitFor(() => expect(confirmBlueprintClaimMock).toHaveBeenCalledWith("test-token", "c1"));
    expect(getDraftBlueprintMock).toHaveBeenCalledTimes(2); // initial + reconcile
  });

  it("rejects a proposed claim through the backend", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("BLUEPRINT_REVIEW"));
    getDraftBlueprintMock.mockResolvedValue(draftBlueprintResponse());
    rejectBlueprintClaimMock.mockResolvedValue({});
    render(<BlueprintPage />);
    fireEvent.click(await screen.findByText("No"));
    await waitFor(() => expect(rejectBlueprintClaimMock).toHaveBeenCalledWith("test-token", "c1"));
  });

  it("confirms the blueprint via the named journey action, not the raw activate endpoint", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("BLUEPRINT_REVIEW"));
    getDraftBlueprintMock.mockResolvedValue(draftBlueprintResponse());
    confirmBlueprintJourneyStepMock.mockResolvedValue(journey("BLUEPRINT_ACTIVE"));

    render(<BlueprintPage />);
    fireEvent.click(await screen.findByText("Confirm and continue"));
    await waitFor(() => expect(confirmBlueprintJourneyStepMock).toHaveBeenCalledWith("test-token", "j1"));
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/business/requirements"));
  });

  it("handles an incomplete/invalid blueprint confirmation (409) by reconciling instead of getting stuck", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("BLUEPRINT_REVIEW"));
    getDraftBlueprintMock.mockResolvedValue(draftBlueprintResponse());
    const { ApiError } = await import("@/lib/api");
    confirmBlueprintJourneyStepMock.mockRejectedValue(new ApiError(409, "Blueprint does not meet the minimum bar yet"));

    render(<BlueprintPage />);
    fireEvent.click(await screen.findByText("Confirm and continue"));
    // Technical backend wording is translated into a next step for the user.
    expect(await screen.findByText(/needs a little more before this can be confirmed/)).toBeInTheDocument();
    expect(getDraftBlueprintMock).toHaveBeenCalledTimes(2); // reconciled, not stuck
  });

  it("redirects back to Discovery while Discovery is still running", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("DISCOVERY_ACTIVE"));
    render(<BlueprintPage />);
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/business/discovery"));
    expect(getDraftBlueprintMock).not.toHaveBeenCalled();
  });

  it("still shows the confirmed Blueprint once the journey is COMPLETED (no current journey)", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(null);
    listBusinessJourneysMock.mockResolvedValueOnce([journey("COMPLETED")]);
    getActiveBlueprintMock.mockResolvedValue({ ...draftBlueprintResponse(), blueprint: { ...draftBlueprintResponse().blueprint, status: "ACTIVE" }, claims: [] });
    render(<BlueprintPage />);
    expect(await screen.findByText("Business Identity")).toBeInTheDocument();
    expect(replaceMock).not.toHaveBeenCalledWith("/business");
  });

  it("lets the owner confirm every proposed statement in one explicit action", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("BLUEPRINT_REVIEW"));
    getDraftBlueprintMock.mockResolvedValue(draftBlueprintResponse());
    confirmBlueprintClaimMock.mockResolvedValue({});
    render(<BlueprintPage />);
    fireEvent.click(await screen.findByText("Everything looks right — confirm all"));
    await waitFor(() => expect(confirmBlueprintClaimMock).toHaveBeenCalledWith("test-token", "c1"));
  });

  it("names the required sections that are still empty instead of letting Confirm fail mysteriously", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("BLUEPRINT_REVIEW"));
    const full = draftBlueprintResponse();
    full.claims = []; // nothing left to confirm — only the empty required section remains
    full.sections.push({ id: "s3", section_key: "INDUSTRY", status: "EMPTY", data: {}, updated_by: null, updated_at: "x" } as never);
    getDraftBlueprintMock.mockResolvedValue(full);
    render(<BlueprintPage />);
    expect(await screen.findByText(/Before you can continue, add something for/)).toBeInTheDocument();
    expect(screen.getByText(/Required Capabilities/, { selector: "strong" })).toBeInTheDocument();
  });

  it("never shows raw null / True / JSON — values read as plain language, with the source of each statement", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("BLUEPRINT_REVIEW"));
    const full = draftBlueprintResponse();
    full.sections[0].data = { demographics: { age_range: "25 to 45", urban: true }, notes: null } as never;
    full.sections[1] = { id: "s2", section_key: "REQUIRED_CAPABILITIES", status: "COMPLETE", data: { payments: true, lead_capture: true }, updated_by: null, updated_at: "x" } as never;
    full.claims[0].value = null as never;
    getDraftBlueprintMock.mockResolvedValue(full);
    render(<BlueprintPage />);
    expect(await screen.findByText(/Age Range: 25 to 45 · Urban: Yes/)).toBeInTheDocument();
    expect(screen.getByText("Not specified")).toBeInTheDocument();
    expect(screen.getByText("Payments")).toBeInTheDocument(); // all-true flags render as a plain list
    expect(screen.getByText(/Tagline — not specified yet/)).toBeInTheDocument();
    const main = document.body.textContent ?? "";
    expect(main).not.toMatch(/\bnull\b|\bundefined\b|\bTrue\b|\bFalse\b|\{"/);
  });

  it("groups sections under business headings and lists unresolved items", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("BLUEPRINT_REVIEW"));
    const full = draftBlueprintResponse();
    full.claims.push({ ...full.claims[0], id: "c9", claim_type: "Unknown", key: "supplier", value: "Which supplier?", status: "PROPOSED" } as never);
    getDraftBlueprintMock.mockResolvedValue(full);
    render(<BlueprintPage />);
    expect(await screen.findByText("Business overview")).toBeInTheDocument();
    expect(screen.getByText("Still unresolved")).toBeInTheDocument();
    expect(screen.getAllByText(/Which supplier\?/).length).toBeGreaterThan(0);
  });

  it("shows read-only confirmed state and a continue-to-requirements action once BLUEPRINT_ACTIVE", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("BLUEPRINT_ACTIVE"));
    getActiveBlueprintMock.mockResolvedValue({ ...draftBlueprintResponse(), blueprint: { ...draftBlueprintResponse().blueprint, status: "ACTIVE" }, claims: [] });
    render(<BlueprintPage />);
    expect(await screen.findByText("Continue to requirements")).toBeInTheDocument();
    expect(screen.queryByText("Edit")).not.toBeInTheDocument();
  });
});
