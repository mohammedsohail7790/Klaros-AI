import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: pushMock }),
  usePathname: () => "/agents/new",
}));

const createAgentMock = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  createAgent: (...args: unknown[]) => createAgentMock(...args),
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

import NewAgentPage from "@/app/agents/new/page";

describe("Create Agent page", () => {
  beforeEach(() => {
    createAgentMock.mockReset();
    pushMock.mockReset();
  });

  it("renders the creation form", () => {
    render(<NewAgentPage />);
    expect(screen.getByText("Agent name")).toBeInTheDocument();
    expect(screen.getByText("What should this agent do?")).toBeInTheDocument();
    expect(screen.getByText("Observe only")).toBeInTheDocument();
  });

  it("submits only backend-supported fields, with no tenant/role/actor spoofing", async () => {
    createAgentMock.mockResolvedValue({ id: "a1" });
    render(<NewAgentPage />);
    fireEvent.change(screen.getByPlaceholderText("e.g. Invoice Follow-up Agent"), {
      target: { value: "Reminder Agent" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create Agent" }));

    await waitFor(() => expect(createAgentMock).toHaveBeenCalled());
    const [token, body] = createAgentMock.mock.calls[0];
    expect(token).toBe("test-token");
    expect(body).toEqual({ name: "Reminder Agent", purpose: "", autonomy_tier: "OBSERVE" });
    expect(body).not.toHaveProperty("tenant_id");
    expect(body).not.toHaveProperty("organization_id");
    expect(body).not.toHaveProperty("role");
    expect(body).not.toHaveProperty("acting_role");
    expect(body).not.toHaveProperty("actor_type");
  });

  it("shows a validation error and does not call the backend without a name", async () => {
    render(<NewAgentPage />);
    fireEvent.click(screen.getByRole("button", { name: "Create Agent" }));
    expect(await screen.findByText("Give this agent a name.")).toBeInTheDocument();
    expect(createAgentMock).not.toHaveBeenCalled();
  });

  it("never claims fully unrestricted autonomy in copy", () => {
    render(<NewAgentPage />);
    expect(screen.queryByText(/unlimited autonomy/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/no restrictions/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/full system access/i)).not.toBeInTheDocument();
  });

  it("navigates to the new agent's detail page on success", async () => {
    createAgentMock.mockResolvedValue({ id: "a42" });
    render(<NewAgentPage />);
    fireEvent.change(screen.getByPlaceholderText("e.g. Invoice Follow-up Agent"), {
      target: { value: "Reminder Agent" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create Agent" }));
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/agents/a42"));
  });
});
