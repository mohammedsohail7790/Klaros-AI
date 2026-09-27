import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const pushMock = vi.fn();
const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: replaceMock }),
  usePathname: () => "/business",
}));

const getCurrentBusinessJourneyMock = vi.fn();
const startBusinessJourneyMock = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  getCurrentBusinessJourney: (...args: unknown[]) => getCurrentBusinessJourneyMock(...args),
  startBusinessJourney: (...args: unknown[]) => startBusinessJourneyMock(...args),
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

import BusinessJourneyEntryPage from "@/app/business/page";

function journey(status: string, overrides: Record<string, unknown> = {}) {
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
    ...overrides,
  };
}

describe("Business journey entry page", () => {
  beforeEach(() => {
    getCurrentBusinessJourneyMock.mockReset();
    startBusinessJourneyMock.mockReset();
    pushMock.mockReset();
    replaceMock.mockReset();
  });

  it("shows the start form when there is no active journey", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(null);
    render(<BusinessJourneyEntryPage />);
    expect(await screen.findByText("Build your business with Klaros")).toBeInTheDocument();
    expect(screen.getByText("Start Discovery")).toBeInTheDocument();
  });

  it("starting a journey never sends tenant_id/role/actor_type — only business_idea", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(null);
    startBusinessJourneyMock.mockResolvedValue(journey("DISCOVERY_ACTIVE"));
    render(<BusinessJourneyEntryPage />);

    const { fireEvent } = await import("@testing-library/react");
    const textarea = await screen.findByPlaceholderText(/subscription meal-prep/i);
    fireEvent.change(textarea, { target: { value: "A coffee shop" } });
    fireEvent.click(screen.getByText("Start Discovery"));

    await waitFor(() => expect(startBusinessJourneyMock).toHaveBeenCalled());
    // Exactly (token, businessIdea) — no tenant/role/actor object anywhere.
    expect(startBusinessJourneyMock).toHaveBeenCalledWith("test-token", "A coffee shop");
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/business/discovery"));
  });

  it("redirects to the authoritative stage for an existing active journey instead of duplicating it", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("BLUEPRINT_REVIEW"));
    render(<BusinessJourneyEntryPage />);
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/business/blueprint"));
    expect(startBusinessJourneyMock).not.toHaveBeenCalled();
  });

  it("shows a completion state for a COMPLETED journey without redirecting away", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("COMPLETED"));
    render(<BusinessJourneyEntryPage />);
    expect(await screen.findByText("Your Klaros business foundation is ready")).toBeInTheDocument();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("shows a restart option for an ABANDONED journey", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("ABANDONED"));
    render(<BusinessJourneyEntryPage />);
    expect(await screen.findByText("Start Discovery")).toBeInTheDocument();
  });

  it("shows an error banner rather than crashing on a backend failure", async () => {
    const { ApiError } = await import("@/lib/api");
    getCurrentBusinessJourneyMock.mockRejectedValue(new ApiError(500, "Internal server error"));
    render(<BusinessJourneyEntryPage />);
    expect(await screen.findByText("Internal server error")).toBeInTheDocument();
  });
});
