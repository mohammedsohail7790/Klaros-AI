import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }), usePathname: () => "/leads" }));
const getBoard = vi.fn();
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error { status: number; constructor(s: number, m: string) { super(m); this.status = s; } },
  getLeadBoard: (...a: unknown[]) => getBoard(...a),
  bulkImportLeads: vi.fn(),
  createLead: vi.fn(),
  listNotifications: vi.fn().mockResolvedValue({ notifications: [] }),
  getUnreadNotificationCount: vi.fn().mockResolvedValue({ count: 0 }),
  markNotificationRead: vi.fn(), markAllNotificationsRead: vi.fn(), dismissNotification: vi.fn(), logout: vi.fn(),
}));
vi.mock("@/lib/useAuth", () => ({
  useAuth: () => ({ token: "t", user: { id: "u", tenant_id: "t1", email: "a@b.c", full_name: "A", role: "OWNER" }, loading: false, error: null }),
}));
import Page from "@/app/leads/page";

const row = (over: Record<string, unknown> = {}) => ({
  id: "4d9d1dfa-0fe6-444e-98f3-c76772837978", name: "Omar Al Farsi", status: "QUALIFIED", source: "WEB", created_at: new Date().toISOString(),
  priority: "HIGH", assigned_to: "Dana Reyes", country: "TR", service: "Hair transplant",
  next_action: { text: "Review provider matches", route: "/leads/4d9d1dfa-0fe6-444e-98f3-c76772837978" }, ai_state: "ESCALATED", ai_label: "Escalated to a person", ...over,
});
const board = (rows: unknown[]) => ({ leads: rows, total: rows.length, limit: 20, offset: 0 });

beforeEach(() => getBoard.mockReset());

describe("Leads — the operating workspace", () => {
  it("shows status, priority, country, next action, AI interaction and assignee for each lead", async () => {
    getBoard.mockResolvedValue(board([row(), row({ id: "x2", name: "Sara Khan", status: "NEW", priority: "LOW", assigned_to: null, country: null, service: null, next_action: null, ai_state: "NOT_CONNECTED", ai_label: "Halla not connected" })]));
    render(<Page />);
    const table = await screen.findByRole("table");
    for (const h of ["Lead", "Status", "Priority", "Country", "Next action", "AI interaction", "Assigned", "Received"]) {
      expect(within(table).getByRole("columnheader", { name: h })).toBeInTheDocument();
    }
    const omar = within(table).getByRole("link", { name: "Omar Al Farsi" }).closest("tr")!;
    expect(omar).toHaveTextContent("Hair transplant · Website");
    expect(omar).toHaveTextContent("Qualified"); // a label, not QUALIFIED
    expect(omar).toHaveTextContent("High");
    expect(omar).toHaveTextContent("TR");
    expect(within(omar).getByRole("link", { name: "Review provider matches" })).toBeInTheDocument();
    expect(omar).toHaveTextContent("Escalated to a person");
    expect(omar).toHaveTextContent("Dana Reyes");
    const sara = within(table).getByRole("link", { name: "Sara Khan" }).closest("tr")!;
    expect(sara).toHaveTextContent("Nothing to do");
    expect(sara).toHaveTextContent("Unassigned");
    expect(sara).toHaveTextContent("Halla not connected");
    expect(sara).toHaveTextContent("—"); // no country → a dash, not "null"
  });

  it("never shows raw ids, null or undefined", async () => {
    getBoard.mockResolvedValue(board([row({ country: null, service: null })]));
    render(<Page />);
    await screen.findByRole("table");
    expect(document.body.textContent).not.toMatch(/\b[0-9a-f]{8}-[0-9a-f]{4}-|\bnull\b|undefined/);
  });

  it("also renders each lead as a card for narrow screens", async () => {
    getBoard.mockResolvedValue(board([row()]));
    render(<Page />);
    const cards = await screen.findByRole("list", { name: "Leads" });
    expect(within(cards).getByRole("link")).toHaveAttribute("href", "/leads/4d9d1dfa-0fe6-444e-98f3-c76772837978");
    expect(cards).toHaveTextContent("Next: Review provider matches");
  });

  it("filters by status, source and search through the real API", async () => {
    getBoard.mockResolvedValue(board([row()]));
    render(<Page />);
    await screen.findByRole("table");
    fireEvent.click(screen.getByRole("button", { name: "New" }));
    await waitFor(() => expect(getBoard).toHaveBeenLastCalledWith("t", expect.objectContaining({ status: "NEW" })));
    fireEvent.change(screen.getByLabelText("Filter by source"), { target: { value: "CHAT" } });
    await waitFor(() => expect(getBoard).toHaveBeenLastCalledWith("t", expect.objectContaining({ status: "NEW", source: "CHAT" })));
    fireEvent.change(screen.getByLabelText("Search leads"), { target: { value: "omar" } });
    await waitFor(() => expect(getBoard).toHaveBeenLastCalledWith("t", expect.objectContaining({ q: "omar" })));
  });

  it("offers to clear filters when nothing matches, and explains a truly empty list", async () => {
    getBoard.mockResolvedValue(board([]));
    render(<Page />);
    expect(await screen.findByText(/No leads yet — they appear here when someone submits your website form/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Lost" }));
    expect(await screen.findByText("No leads match these filters.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(getBoard).toHaveBeenLastCalledWith("t", expect.objectContaining({ status: undefined })));
  });

  it("announces a failure and offers a retry", async () => {
    getBoard.mockRejectedValueOnce(new Error("down"));
    getBoard.mockResolvedValue(board([row()]));
    render(<Page />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Unable to load leads.");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("table")).toBeInTheDocument();
  });
});
