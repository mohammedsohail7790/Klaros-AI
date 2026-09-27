import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const pushMock = vi.fn();
const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: replaceMock }),
  usePathname: () => "/business/recommendations",
}));

const getCurrentBusinessJourneyMock = vi.fn();
const listRecommendationsMock = vi.fn();
const acceptRecommendationMock = vi.fn();
const rejectRecommendationMock = vi.fn();
const completeBusinessJourneyMock = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  getCurrentBusinessJourney: (...args: unknown[]) => getCurrentBusinessJourneyMock(...args),
  listRecommendations: (...args: unknown[]) => listRecommendationsMock(...args),
  acceptRecommendation: (...args: unknown[]) => acceptRecommendationMock(...args),
  rejectRecommendation: (...args: unknown[]) => rejectRecommendationMock(...args),
  completeBusinessJourney: (...args: unknown[]) => completeBusinessJourneyMock(...args),
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

import RecommendationsPage from "@/app/business/recommendations/page";

function journey(status: string) {
  return {
    id: "j1",
    status,
    discovery_session_id: "d1",
    blueprint_id: "b1",
    recommendation_run_id: "r1",
    created_by: "u1",
    completed_at: null,
    abandoned_at: null,
    last_error: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

function rec(overrides: Record<string, unknown> = {}) {
  return {
    id: "rec1",
    run_id: "r1",
    blueprint_id: "b1",
    blueprint_version: 1,
    type: "INTEGRATION",
    capability_key: "payments",
    provider_key: "stripe",
    provider_implementation_status: "IMPLEMENTED",
    tool_name: null,
    what: "Connect Stripe for payments",
    why: "You said you'll take online payments",
    based_on: null,
    dependencies: [],
    cost_estimate: null,
    required: true,
    alternatives: null,
    confidence: 0.8,
    source: "BASELINE_RULE",
    source_vertical_key: null,
    status: "PROPOSED",
    decided_by: null,
    decided_at: null,
    rejection_reason: null,
    created_at: "x",
    updated_at: "x",
    ...overrides,
  };
}

describe("Recommendations page", () => {
  beforeEach(() => {
    getCurrentBusinessJourneyMock.mockReset();
    listRecommendationsMock.mockReset();
    acceptRecommendationMock.mockReset();
    rejectRecommendationMock.mockReset();
    completeBusinessJourneyMock.mockReset();
    replaceMock.mockReset();
  });

  it("renders recommendation cards with real backend fields", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("RECOMMENDATIONS_READY"));
    listRecommendationsMock.mockResolvedValue([rec()]);
    render(<RecommendationsPage />);
    expect(await screen.findByText("Connect Stripe for payments")).toBeInTheDocument();
    expect(screen.getByText("You said you'll take online payments")).toBeInTheDocument();
  });

  it("shows Required vs Optional truthfully from the backend field", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("RECOMMENDATIONS_READY"));
    listRecommendationsMock.mockResolvedValue([rec({ required: true }), rec({ id: "rec2", required: false, what: "Optional add-on" })]);
    render(<RecommendationsPage />);
    expect(await screen.findByText("Required")).toBeInTheDocument();
    expect(screen.getByText("Optional")).toBeInTheDocument();
  });

  it("shows an honest message instead of fabricating a cost when cost_estimate is null", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("RECOMMENDATIONS_READY"));
    listRecommendationsMock.mockResolvedValue([rec({ cost_estimate: null })]);
    render(<RecommendationsPage />);
    expect(await screen.findByText("Cost estimate unavailable")).toBeInTheDocument();
  });

  it("does not render an Alternatives block when none were returned", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("RECOMMENDATIONS_READY"));
    listRecommendationsMock.mockResolvedValue([rec({ alternatives: null })]);
    render(<RecommendationsPage />);
    await screen.findByText("Connect Stripe for payments");
    expect(screen.queryByText("Alternatives")).not.toBeInTheDocument();
  });

  it("accepts a recommendation through the backend and reconciles its displayed status", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("RECOMMENDATIONS_READY"));
    listRecommendationsMock.mockResolvedValue([rec()]);
    acceptRecommendationMock.mockResolvedValue(rec({ status: "ACCEPTED", decided_by: "u1" }));

    render(<RecommendationsPage />);
    fireEvent.click(await screen.findByText("Accept"));
    await waitFor(() => expect(acceptRecommendationMock).toHaveBeenCalledWith("test-token", "rec1"));
    expect(await screen.findByText("ACCEPTED")).toBeInTheDocument();
  });

  it("rejects a recommendation through the backend", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("RECOMMENDATIONS_READY"));
    listRecommendationsMock.mockResolvedValue([rec()]);
    rejectRecommendationMock.mockResolvedValue(rec({ status: "REJECTED" }));

    render(<RecommendationsPage />);
    fireEvent.click(await screen.findByText("Reject"));
    await waitFor(() => expect(rejectRecommendationMock).toHaveBeenCalledWith("test-token", "rec1"));
  });

  it("redirects away when recommendations haven't actually been generated yet", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("BLUEPRINT_ACTIVE"));
    render(<RecommendationsPage />);
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/business/blueprint"));
    expect(listRecommendationsMock).not.toHaveBeenCalled();
  });

  it("shows a loading state while recommendations are being fetched", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("RECOMMENDATIONS_READY"));
    let resolveList: (v: unknown) => void = () => {};
    listRecommendationsMock.mockReturnValue(new Promise((resolve) => (resolveList = resolve)));
    render(<RecommendationsPage />);
    expect(screen.getByLabelText("Loading")).toBeInTheDocument();
    resolveList([]);
  });
});
