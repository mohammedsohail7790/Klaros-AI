import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const pushMock = vi.fn();
const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: replaceMock }),
  usePathname: () => "/business/blueprint",
}));

const getCurrentBusinessJourneyMock = vi.fn();
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
    expect(await screen.findByText("Not filled in yet.")).toBeInTheDocument();
  });

  it("confirms a proposed claim through the backend and reconciles", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("BLUEPRINT_REVIEW"));
    getDraftBlueprintMock.mockResolvedValue(draftBlueprintResponse());
    confirmBlueprintClaimMock.mockResolvedValue({});
    render(<BlueprintPage />);
    fireEvent.click(await screen.findByText("Confirm"));
    await waitFor(() => expect(confirmBlueprintClaimMock).toHaveBeenCalledWith("test-token", "c1"));
    expect(getDraftBlueprintMock).toHaveBeenCalledTimes(2); // initial + reconcile
  });

  it("rejects a proposed claim through the backend", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("BLUEPRINT_REVIEW"));
    getDraftBlueprintMock.mockResolvedValue(draftBlueprintResponse());
    rejectBlueprintClaimMock.mockResolvedValue({});
    render(<BlueprintPage />);
    fireEvent.click(await screen.findByText("Reject"));
    await waitFor(() => expect(rejectBlueprintClaimMock).toHaveBeenCalledWith("test-token", "c1"));
  });

  it("confirms the blueprint via the named journey action, not the raw activate endpoint", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("BLUEPRINT_REVIEW"));
    getDraftBlueprintMock.mockResolvedValue(draftBlueprintResponse());
    confirmBlueprintJourneyStepMock.mockResolvedValue(journey("BLUEPRINT_ACTIVE"));

    render(<BlueprintPage />);
    fireEvent.click(await screen.findByText("Confirm Blueprint"));
    await waitFor(() => expect(confirmBlueprintJourneyStepMock).toHaveBeenCalledWith("test-token", "j1"));
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/business/blueprint"));
  });

  it("handles an incomplete/invalid blueprint confirmation (409) by reconciling instead of getting stuck", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("BLUEPRINT_REVIEW"));
    getDraftBlueprintMock.mockResolvedValue(draftBlueprintResponse());
    const { ApiError } = await import("@/lib/api");
    confirmBlueprintJourneyStepMock.mockRejectedValue(new ApiError(409, "Blueprint does not meet the minimum bar yet"));

    render(<BlueprintPage />);
    fireEvent.click(await screen.findByText("Confirm Blueprint"));
    expect(await screen.findByText("Blueprint does not meet the minimum bar yet")).toBeInTheDocument();
  });

  it("redirects away for a stage the journey isn't actually in", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("DISCOVERY_ACTIVE"));
    render(<BlueprintPage />);
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/business/discovery"));
    expect(getDraftBlueprintMock).not.toHaveBeenCalled();
  });

  it("shows read-only confirmed state and a generate-recommendations action once BLUEPRINT_ACTIVE", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("BLUEPRINT_ACTIVE"));
    getActiveBlueprintMock.mockResolvedValue({ ...draftBlueprintResponse(), blueprint: { ...draftBlueprintResponse().blueprint, status: "ACTIVE" }, claims: [] });
    render(<BlueprintPage />);
    expect(await screen.findByText("Generate recommendations")).toBeInTheDocument();
    expect(screen.queryByText("Edit")).not.toBeInTheDocument();
  });
});
