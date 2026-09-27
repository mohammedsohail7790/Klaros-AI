import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: pushMock }),
  usePathname: () => "/agents",
}));

const listAgentsMock = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  listAgents: (...args: unknown[]) => listAgentsMock(...args),
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

import AgentsPage from "@/app/agents/page";
import { ApiError } from "@/lib/api";

function agent(overrides: Record<string, unknown> = {}) {
  return {
    id: "a1",
    name: "Invoice Follow-up Agent",
    purpose: "Chases unpaid invoices",
    status: "DRAFT",
    autonomy_tier: "OBSERVE",
    acting_role: "OWNER",
    current_version_id: null,
    source_blueprint_id: null,
    source_blueprint_version: null,
    source_recommendation_id: null,
    created_by: "u1",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

describe("Agents list page", () => {
  beforeEach(() => {
    listAgentsMock.mockReset();
    pushMock.mockReset();
  });

  it("loads and renders real agents from the backend", async () => {
    listAgentsMock.mockResolvedValue([agent()]);
    render(<AgentsPage />);
    expect(await screen.findByText("Invoice Follow-up Agent")).toBeInTheDocument();
    expect(screen.getByText("Chases unpaid invoices")).toBeInTheDocument();
  });

  it("shows the honest empty state with no fabricated metrics", async () => {
    listAgentsMock.mockResolvedValue([]);
    render(<AgentsPage />);
    expect(await screen.findByText("No agents yet. Create an agent to automate a governed business task.")).toBeInTheDocument();
    expect(screen.queryByText(/success rate/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/health score/i)).not.toBeInTheDocument();
  });

  it("shows an error state on failure instead of a blank page", async () => {
    listAgentsMock.mockRejectedValue(new ApiError(500, "Server error"));
    render(<AgentsPage />);
    expect(await screen.findByText("Server error")).toBeInTheDocument();
  });

  it("navigates to the agent detail page on click", async () => {
    listAgentsMock.mockResolvedValue([agent()]);
    render(<AgentsPage />);
    fireEvent.click(await screen.findByText("Invoice Follow-up Agent"));
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/agents/a1"));
  });
});
