import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const pushMock = vi.fn();
const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: replaceMock }),
  usePathname: () => "/business/discovery",
}));

const getCurrentBusinessJourneyMock = vi.fn();
const getDiscoverySessionMock = vi.fn();
const answerDiscoveryQuestionMock = vi.fn();
const completeDiscoveryJourneyStepMock = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  getCurrentBusinessJourney: (...args: unknown[]) => getCurrentBusinessJourneyMock(...args),
  getDiscoverySession: (...args: unknown[]) => getDiscoverySessionMock(...args),
  answerDiscoveryQuestion: (...args: unknown[]) => answerDiscoveryQuestionMock(...args),
  completeDiscoveryJourneyStep: (...args: unknown[]) => completeDiscoveryJourneyStepMock(...args),
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

import DiscoveryPage from "@/app/business/discovery/page";

function journey(status: string) {
  return {
    id: "j1",
    status,
    discovery_session_id: "d1",
    blueprint_id: null,
    recommendation_run_id: null,
    created_by: "u1",
    completed_at: null,
    abandoned_at: null,
    last_error: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

describe("Discovery page", () => {
  beforeEach(() => {
    getCurrentBusinessJourneyMock.mockReset();
    getDiscoverySessionMock.mockReset();
    answerDiscoveryQuestionMock.mockReset();
    completeDiscoveryJourneyStepMock.mockReset();
    pushMock.mockReset();
    replaceMock.mockReset();
  });

  it("renders the current question from the backend", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("DISCOVERY_ACTIVE"));
    getDiscoverySessionMock.mockResolvedValue({
      session: { id: "d1", blueprint_id: null, status: "ACTIVE", business_idea: "A bakery", questions_asked: 1, max_questions: 8, created_at: "x", updated_at: "x" },
      turns: [{ id: "t1", sequence: 1, kind: "QUESTION_ANSWER", question: "Who is your target customer?", answer: null, extraction_error: null, created_at: "x" }],
    });

    render(<DiscoveryPage />);
    expect(await screen.findByText("Who is your target customer?")).toBeInTheDocument();
  });

  it("submits an answer through the existing Discovery API and shows the next question", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("DISCOVERY_ACTIVE"));
    getDiscoverySessionMock.mockResolvedValue({
      session: { id: "d1", blueprint_id: null, status: "ACTIVE", business_idea: "A bakery", questions_asked: 1, max_questions: 8, created_at: "x", updated_at: "x" },
      turns: [{ id: "t1", sequence: 1, kind: "QUESTION_ANSWER", question: "Who is your target customer?", answer: null, extraction_error: null, created_at: "x" }],
    });
    answerDiscoveryQuestionMock.mockResolvedValue({
      session_id: "d1",
      session_status: "ACTIVE",
      questions_asked: 2,
      turn_sequence: 2,
      proposed_claim_ids: [],
      next_question: "What's your budget?",
      extraction_available: true,
      extraction_error: null,
    });

    render(<DiscoveryPage />);
    const textarea = await screen.findByLabelText("Your answer");
    fireEvent.change(textarea, { target: { value: "Young professionals" } });
    fireEvent.click(screen.getByText("Continue"));

    await waitFor(() => expect(answerDiscoveryQuestionMock).toHaveBeenCalledWith("test-token", "d1", "Young professionals"));
    expect(await screen.findByText("What's your budget?")).toBeInTheDocument();
  });

  it("advances the journey via complete-discovery when the session completes, never inferring it locally", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("DISCOVERY_ACTIVE"));
    getDiscoverySessionMock.mockResolvedValue({
      session: { id: "d1", blueprint_id: null, status: "ACTIVE", business_idea: "A bakery", questions_asked: 8, max_questions: 8, created_at: "x", updated_at: "x" },
      turns: [{ id: "t1", sequence: 8, kind: "QUESTION_ANSWER", question: "Last question?", answer: null, extraction_error: null, created_at: "x" }],
    });
    answerDiscoveryQuestionMock.mockResolvedValue({
      session_id: "d1",
      session_status: "COMPLETED",
      questions_asked: 8,
      turn_sequence: 9,
      proposed_claim_ids: [],
      next_question: null,
      extraction_available: true,
      extraction_error: null,
    });
    completeDiscoveryJourneyStepMock.mockResolvedValue(journey("BLUEPRINT_REVIEW"));

    render(<DiscoveryPage />);
    const textarea = await screen.findByLabelText("Your answer");
    fireEvent.change(textarea, { target: { value: "Final answer" } });
    fireEvent.click(screen.getByText("Continue"));

    await waitFor(() => expect(completeDiscoveryJourneyStepMock).toHaveBeenCalledWith("test-token", "j1"));
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/business/blueprint"));
  });

  it("redirects away when the journey is not actually at the Discovery stage (direct-URL guard)", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("RECOMMENDATIONS_READY"));
    render(<DiscoveryPage />);
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/business/recommendations"));
    expect(getDiscoverySessionMock).not.toHaveBeenCalled();
  });

  it("shows an error state when the answer submission fails", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("DISCOVERY_ACTIVE"));
    getDiscoverySessionMock.mockResolvedValue({
      session: { id: "d1", blueprint_id: null, status: "ACTIVE", business_idea: "A bakery", questions_asked: 1, max_questions: 8, created_at: "x", updated_at: "x" },
      turns: [{ id: "t1", sequence: 1, kind: "QUESTION_ANSWER", question: "Who is your target customer?", answer: null, extraction_error: null, created_at: "x" }],
    });
    const { ApiError } = await import("@/lib/api");
    answerDiscoveryQuestionMock.mockRejectedValue(new ApiError(500, "Internal server error"));

    render(<DiscoveryPage />);
    const textarea = await screen.findByLabelText("Your answer");
    fireEvent.change(textarea, { target: { value: "Young professionals" } });
    fireEvent.click(screen.getByText("Continue"));

    expect(await screen.findByText("Internal server error")).toBeInTheDocument();
  });
});
