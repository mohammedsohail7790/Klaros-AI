import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { opsFixture, overviewFixture } from "@/lib/__tests__/opsFixtures";

const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: replaceMock }),
  usePathname: () => "/business/home",
}));
const getBuilderOverviewMock = vi.fn();
const getBusinessOperationsMock = vi.fn();
const getLeadBoardMock = vi.fn();
const getMyWebsiteMock = vi.fn();
const listVersionsMock = vi.fn();
const publishMock = vi.fn();
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  getBuilderOverview: (...a: unknown[]) => getBuilderOverviewMock(...a),
  getBusinessOperations: (...a: unknown[]) => getBusinessOperationsMock(...a),
  getLeadBoard: (...a: unknown[]) => getLeadBoardMock(...a),
  getMyWebsite: (...a: unknown[]) => getMyWebsiteMock(...a),
  listWebsiteVersions: (...a: unknown[]) => listVersionsMock(...a),
  publishWebsiteVersion: (...a: unknown[]) => publishMock(...a),
  listNotifications: vi.fn().mockResolvedValue({ notifications: [] }),
  getUnreadNotificationCount: vi.fn().mockResolvedValue({ count: 0 }),
  markNotificationRead: vi.fn(),
  markAllNotificationsRead: vi.fn(),
  dismissNotification: vi.fn(),
  logout: vi.fn(),
}));
vi.mock("@/lib/useAuth", () => ({
  useAuth: () => ({ token: "t", user: { id: "u", tenant_id: "tenant-1", email: "a@b.c", full_name: "A", role: "OWNER" }, loading: false, error: null }),
}));

import BusinessHomePage from "@/app/business/home/page";

const live = { website: { exists: true, published: true, has_draft: false, pages: ["home"] } };
const launched = (over: Record<string, unknown> = {}) => overviewFixture({ ...live, ...over });
// Not launched: a draft website exists, nothing else outstanding except what the test adds.
const draftOverview = (over: Record<string, unknown> = {}) => {
  const base = overviewFixture();
  return overviewFixture({
    website: { exists: true, published: false, has_draft: true, pages: ["home"] },
    launch: {
      ...base.launch, launched: false, ready: false, verdict: "NOT_READY",
      items: [
        { key: "blueprint", label: "Business Blueprint", status: "READY", detail: "Confirmed.", required: true, route: "/business/blueprint", done: true },
        { key: "requirements", label: "Requirements", status: "READY", detail: "3 requirements.", required: true, route: "/business/requirements", done: true },
        { key: "website", label: "Website", status: "CONFIGURATION_REQUIRED", detail: "A draft exists but isn't published — publish it to go live.", required: true, route: "/website", done: false },
      ],
    },
    ...over,
  });
};
const board = (rows: unknown[] = [{ id: "l1", name: "Layla Hassan", status: "QUALIFIED", source: "WEB", created_at: new Date().toISOString(), priority: "MEDIUM", assigned_to: null, country: "IN", service: "Dental implants", next_action: { text: "Review provider matches", route: "/leads/l1" }, ai_state: "NOT_CONNECTED", ai_label: "Halla not connected" }]) => ({ leads: rows, total: rows.length, limit: 20, offset: 0 });

describe("Business Home — the command center", () => {
  beforeEach(() => {
    for (const m of [getBuilderOverviewMock, getBusinessOperationsMock, getLeadBoardMock, getMyWebsiteMock, listVersionsMock, publishMock, replaceMock]) m.mockReset();
    getBusinessOperationsMock.mockResolvedValue(opsFixture({ attention: [{ id: "new-leads", text: "2 new leads waiting for a first response", route: "/leads?status=NEW", count: 2 }] }));
    getLeadBoardMock.mockResolvedValue(board());
  });

  it("answers 'what is happening now': overview, health, action center, architecture, leads, activity", async () => {
    getBuilderOverviewMock.mockResolvedValue(launched());
    render(<BusinessHomePage />);
    expect(await screen.findByRole("heading", { level: 1, name: "Aurora Medical Travel" })).toBeInTheDocument();
    for (const h of ["Business health", "Action center", "How your business operates", "Leads to move forward", "Recent activity", "Launch readiness"]) {
      expect(screen.getByRole("heading", { level: 2, name: h })).toBeInTheDocument();
    }
    expect(screen.getByText("Stage:").parentElement).toHaveTextContent("Operating");
  });

  it("business health shows real counts and an honest workforce tile", async () => {
    getBuilderOverviewMock.mockResolvedValue(launched());
    render(<BusinessHomePage />);
    const health = await screen.findByRole("list", { name: "Business health" });
    await within(health).findByText("3 new this week");
    expect(within(health).getByText("Leads").closest("a")).toHaveTextContent("4");
    const wf = within(health).getByText("AI workforce").closest("a")!;
    expect(wf).toHaveTextContent("Integration required");
    expect(wf).not.toHaveTextContent("Connected");
    expect(within(health).getByText("Website").closest("a")).toHaveTextContent("Published");
    expect(within(health).getByText("Workflows").closest("a")).toHaveTextContent("1 of 1 running");
  });

  it("action center lists only real things, urgent first, each with a way to act", async () => {
    getBuilderOverviewMock.mockResolvedValue(launched());
    getBusinessOperationsMock.mockResolvedValue(
      opsFixture({ attention: [{ id: "new-leads", text: "2 new leads waiting for a first response", route: "/leads?status=NEW", count: 2 }, { id: "failed-runs", text: "1 workflow run failed", route: "/business/workflows", count: 1 }] })
    );
    render(<BusinessHomePage />);
    const list = await screen.findByRole("list", { name: "Action center" });
    const rows = within(list).getAllByRole("listitem").map((li) => li.textContent);
    expect(rows[0]).toMatch(/1 workflow run failed/);
    expect(rows[1]).toMatch(/2 new leads waiting/);
    expect(rows.join(" ")).toMatch(/Halla needs to be integrated/);
    expect(rows.join(" ")).toMatch(/Google Calendar isn.t connected/);
    expect(within(list).getAllByRole("link").every((a) => a.getAttribute("href"))).toBe(true);
  });

  it("says so when nothing needs a person", async () => {
    getBuilderOverviewMock.mockResolvedValue(launched({ requirements: [], workforce: { ...overviewFixture().workforce, status: "CONNECTED", adapter_implemented: true } }));
    getBusinessOperationsMock.mockResolvedValue(opsFixture());
    render(<BusinessHomePage />);
    expect(await screen.findByText(/Nothing is waiting on you right now/)).toBeInTheDocument();
  });

  it("shows the architecture as a chain of linked, honestly-labelled parts", async () => {
    getBuilderOverviewMock.mockResolvedValue(launched());
    render(<BusinessHomePage />);
    const chain = await screen.findByRole("list", { name: "How your business operates" });
    const nodes = within(chain).getAllByRole("link").map((a) => a.textContent ?? "");
    expect(nodes[0]).toMatch(/Website.*Published/);
    expect(nodes[1]).toMatch(/AI workforce.*Integration required/);
    expect(within(chain).getByText("AI workforce").closest("a")).toHaveAttribute("href", "/workforce");
    expect(screen.getByRole("link", { name: /Open the Business Map/ })).toHaveAttribute("href", "/business/map");
  });

  it("lists leads with their next step from the lead board — names, never raw ids", async () => {
    getBuilderOverviewMock.mockResolvedValue(launched());
    render(<BusinessHomePage />);
    const list = await screen.findByRole("list", { name: "Leads with a next step" });
    expect(within(list).getByRole("link", { name: "Layla Hassan" })).toHaveAttribute("href", "/leads/l1");
    expect(list).toHaveTextContent("Next: Review provider matches");
    expect(list).toHaveTextContent("Dental implants");
    expect(document.body.textContent).not.toMatch(/\b[0-9a-f]{8}-[0-9a-f]{4}-/);
  });

  it("shows an empty and an error state for leads", async () => {
    getBuilderOverviewMock.mockResolvedValue(launched());
    getLeadBoardMock.mockResolvedValue(board([]));
    const { unmount } = render(<BusinessHomePage />);
    expect(await screen.findByText("No lead needs a next step right now.")).toBeInTheDocument();
    unmount();
    getLeadBoardMock.mockImplementation(async () => {
      throw new Error("down");
    });
    render(<BusinessHomePage />);
    expect(await screen.findByText("We couldn't load your leads just now.")).toBeInTheDocument();
  });

  it("quick actions point at real pages", async () => {
    getBuilderOverviewMock.mockResolvedValue(launched());
    render(<BusinessHomePage />);
    const q = await screen.findByRole("list", { name: "Quick actions" });
    expect(within(q).getByRole("link", { name: "View leads" })).toHaveAttribute("href", "/leads");
    expect(within(q).getByRole("link", { name: "Configure AI workforce" })).toHaveAttribute("href", "/workforce");
    expect(within(q).getByRole("link", { name: "Connect integration" })).toHaveAttribute("href", "/business/integrations");
    await waitFor(() => expect(within(q).getByRole("link", { name: "Open patient enquiries" })).toHaveAttribute("href", "/medical-tourism/leads"));
  });

  it("recent activity is a timeline of what actually happened", async () => {
    getBuilderOverviewMock.mockResolvedValue(launched());
    getBusinessOperationsMock.mockResolvedValue(
      opsFixture({ activity: [{ at: new Date().toISOString(), kind: "ai", text: "Halla qualified the lead: Omar (simulated)", route: "/leads/l1" }, { at: new Date().toISOString(), kind: "lead", text: "Lead received: Omar", route: "/leads/l1" }] })
    );
    render(<BusinessHomePage />);
    const tl = await screen.findByRole("list", { name: "Recent activity" });
    expect(tl).toHaveTextContent("Halla qualified the lead: Omar (simulated)");
    expect(tl).toHaveTextContent("Lead received: Omar");
  });

  it("shows empty activity wording, not invented events", async () => {
    getBuilderOverviewMock.mockResolvedValue(launched());
    getBusinessOperationsMock.mockResolvedValue(opsFixture({ activity: [] }));
    render(<BusinessHomePage />);
    expect(await screen.findByText(/Nothing has happened yet/)).toBeInTheDocument();
  });

  it("explains an operations failure without blanking the page", async () => {
    getBuilderOverviewMock.mockResolvedValue(launched());
    getBusinessOperationsMock.mockImplementation(async () => {
      throw new Error("down");
    });
    render(<BusinessHomePage />);
    expect(await screen.findByText(/couldn't load your operations/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Aurora Medical Travel" })).toBeInTheDocument();
  });

  it("sends a tenant with no business journey to the builder", async () => {
    getBuilderOverviewMock.mockResolvedValue(launched({ journey: null }));
    render(<BusinessHomePage />);
    await vi.waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/business"));
  });

  it("explains a load failure and offers to retry", async () => {
    getBuilderOverviewMock.mockImplementation(async () => {
      throw new Error("down");
    });
    render(<BusinessHomePage />);
    expect(await screen.findByText(/couldn't load your business/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});

describe("Business Home — launch readiness and the controlled Launch action", () => {
  beforeEach(() => {
    for (const m of [getBuilderOverviewMock, getBusinessOperationsMock, getLeadBoardMock, getMyWebsiteMock, listVersionsMock, publishMock, replaceMock]) m.mockReset();
    getBusinessOperationsMock.mockResolvedValue(opsFixture());
    getLeadBoardMock.mockResolvedValue(board());
  });

  it("before launch the readiness panel comes first and lists the seven groups", async () => {
    getBuilderOverviewMock.mockResolvedValue(draftOverview());
    render(<BusinessHomePage />);
    const panel = await screen.findByRole("list", { name: "Launch readiness" });
    for (const g of ["Business", "Architecture", "Website", "Integrations", "AI workforce", "Workflows", "Data"]) {
      expect(within(panel).getByText(g)).toBeInTheDocument();
    }
    expect(within(panel).getByText("Requires Halla integration")).toBeInTheDocument();
    expect(screen.getByText(/Setting up|Not launched yet/)).toBeInTheDocument();
    expect(screen.queryByText("Your business is live.")).not.toBeInTheDocument();
  });

  it("Launch is enabled only when the website draft exists and nothing else blocks; it never launches on one click", async () => {
    getBuilderOverviewMock.mockResolvedValue(draftOverview());
    getMyWebsiteMock.mockResolvedValue({ id: "site-1" });
    listVersionsMock.mockResolvedValue([{ id: "v1", status: "PUBLISHED" }, { id: "v2", status: "DRAFT" }]);
    publishMock.mockResolvedValue({ id: "v2", status: "PUBLISHED" });
    render(<BusinessHomePage />);
    const btn = await screen.findByRole("button", { name: /Launch Business/ });
    expect(btn).toBeEnabled();
    fireEvent.click(btn);
    expect(publishMock).not.toHaveBeenCalled(); // an explicit confirmation comes first
    expect(screen.getByRole("alertdialog")).toHaveTextContent(/publishes your website/);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(publishMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /Launch Business/ }));
    fireEvent.click(screen.getByRole("button", { name: "Yes, launch" }));
    await waitFor(() => expect(publishMock).toHaveBeenCalledWith("t", "site-1", "v2")); // the DRAFT version, via the existing API
    await waitFor(() => expect(getBuilderOverviewMock.mock.calls.length).toBeGreaterThan(1)); // state is re-read, not assumed
  });

  it("explains why Launch is disabled: no website yet / something else is outstanding", async () => {
    getBuilderOverviewMock.mockResolvedValue(draftOverview({ website: { exists: false, published: false, has_draft: false, pages: [] } }));
    const { unmount } = render(<BusinessHomePage />);
    const btn = await screen.findByRole("button", { name: /Launch Business/ });
    expect(btn).toBeDisabled();
    expect(screen.getByText(/Generate your website first/)).toBeInTheDocument();
    unmount();
    const base = draftOverview();
    getBuilderOverviewMock.mockResolvedValue(
      draftOverview({ launch: { ...base.launch, items: base.launch.items.map((i) => (i.key === "blueprint" ? { ...i, status: "NOT_READY", done: false } : i)) } })
    );
    render(<BusinessHomePage />);
    expect(await screen.findByRole("button", { name: /Launch Business/ })).toBeDisabled();
    expect(screen.getByText(/Finish this first: business/)).toBeInTheDocument();
  });

  it("a failed launch changes nothing and says so", async () => {
    getBuilderOverviewMock.mockResolvedValue(draftOverview());
    getMyWebsiteMock.mockResolvedValue({ id: "site-1" });
    listVersionsMock.mockResolvedValue([{ id: "v2", status: "DRAFT" }]);
    publishMock.mockRejectedValue(new Error("boom"));
    render(<BusinessHomePage />);
    fireEvent.click(await screen.findByRole("button", { name: /Launch Business/ }));
    fireEvent.click(screen.getByRole("button", { name: "Yes, launch" }));
    expect(await screen.findByText(/Nothing was changed/)).toBeInTheDocument();
  });

  it("once live it says so and links to the public site — and the checklist stays honest", async () => {
    getBuilderOverviewMock.mockResolvedValue(launched());
    render(<BusinessHomePage />);
    expect(await screen.findByText("Your business is live.")).toBeInTheDocument();
    const a = screen.getByRole("link", { name: /View live website/ });
    expect(a).toHaveAttribute("href", "/w/tenant-1");
    expect(a).toHaveAttribute("target", "_blank");
    expect(screen.queryByRole("button", { name: /Launch Business/ })).not.toBeInTheDocument();
    expect(screen.getByText("Live — launch checklist incomplete")).toBeInTheDocument(); // a data item is still outstanding
  });
});
