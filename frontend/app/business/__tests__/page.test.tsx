import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const pushMock = vi.fn();
const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: replaceMock }),
  usePathname: () => "/business",
}));

const getCurrentBusinessJourneyMock = vi.fn();
const listBusinessJourneysMock = vi.fn().mockResolvedValue([]);
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
  listBusinessJourneys: (...args: unknown[]) => listBusinessJourneysMock(...args),
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
    expect(await screen.findByText("What are you building?")).toBeInTheDocument();
    expect(screen.getByText("Start building")).toBeInTheDocument();
  });

  it("starting a journey never sends tenant_id/role/actor_type — only business_idea", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(null);
    startBusinessJourneyMock.mockResolvedValue(journey("DISCOVERY_ACTIVE"));
    render(<BusinessJourneyEntryPage />);

    const { fireEvent } = await import("@testing-library/react");
    const textarea = await screen.findByLabelText("Your business idea");
    fireEvent.change(textarea, { target: { value: "A coffee shop" } });
    fireEvent.click(screen.getByText("Start building"));

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

  it("sends a COMPLETED journey to the business home instead of the start form", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("COMPLETED"));
    render(<BusinessJourneyEntryPage />);
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/business/home"));
    expect(screen.queryByText("Start building")).not.toBeInTheDocument();
  });

  it("sends an established business (completed journey, no current one) to its home, never to a blank start form", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(null);
    listBusinessJourneysMock.mockResolvedValueOnce([journey("COMPLETED")]);
    render(<BusinessJourneyEntryPage />);
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/business/home"));
    expect(screen.queryByText("Start building")).not.toBeInTheDocument();
  });

  it("an example idea only fills the text box — it never starts anything by itself", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(null);
    const { fireEvent } = await import("@testing-library/react");
    render(<BusinessJourneyEntryPage />);
    fireEvent.click(await screen.findByText(/dropshipping business using a supplier catalog/i));
    expect((screen.getByLabelText("Your business idea") as HTMLTextAreaElement).value).toMatch(/dropshipping/i);
    expect(startBusinessJourneyMock).not.toHaveBeenCalled();
  });

  it("pre-fills the idea typed on the marketing site and clears it", async () => {
    sessionStorage.setItem("klaros_pending_idea", "A boutique hotel booking engine");
    getCurrentBusinessJourneyMock.mockResolvedValue(null);
    render(<BusinessJourneyEntryPage />);
    const box = (await screen.findByLabelText("Your business idea")) as HTMLTextAreaElement;
    await waitFor(() => expect(box.value).toBe("A boutique hotel booking engine"));
    expect(sessionStorage.getItem("klaros_pending_idea")).toBeNull();
  });

  it("shows a restart option for an ABANDONED journey", async () => {
    getCurrentBusinessJourneyMock.mockResolvedValue(journey("ABANDONED"));
    render(<BusinessJourneyEntryPage />);
    expect(await screen.findByText("Start building")).toBeInTheDocument();
  });

  it("shows an error banner rather than crashing on a backend failure", async () => {
    const { ApiError } = await import("@/lib/api");
    getCurrentBusinessJourneyMock.mockRejectedValue(new ApiError(500, "Internal server error"));
    render(<BusinessJourneyEntryPage />);
    expect(await screen.findByText("Internal server error")).toBeInTheDocument();
  });
});
