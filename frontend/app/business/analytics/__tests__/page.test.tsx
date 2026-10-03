import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { emptyLeads, opsFixture } from "@/lib/__tests__/opsFixtures";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }), usePathname: () => "/business/analytics" }));
const getOps = vi.fn();
const getOverview = vi.fn();
const createStarter = vi.fn();
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error { status: number; constructor(s: number, m: string) { super(m); this.status = s; } },
  getBusinessOperations: (...a: unknown[]) => getOps(...a),
  getBuilderOverview: (...a: unknown[]) => getOverview(...a),
  createStarterWorkflow: (...a: unknown[]) => createStarter(...a),
  listNotifications: vi.fn().mockResolvedValue({ notifications: [] }),
  getUnreadNotificationCount: vi.fn().mockResolvedValue({ count: 0 }),
  markNotificationRead: vi.fn(), markAllNotificationsRead: vi.fn(), dismissNotification: vi.fn(), logout: vi.fn(),
}));
vi.mock("@/lib/useAuth", () => ({
  useAuth: () => ({ token: "t", user: { id: "u", tenant_id: "t1", email: "a@b.c", full_name: "A", role: "OWNER" }, loading: false, error: null }),
}));
const overview = (requirements: unknown[] = []) => ({ journey: { id: "j", status: "COMPLETED" }, requirements, stages: [], next_actions: [] });
import Page from "@/app/business/analytics/page";

beforeEach(() => {
  getOps.mockReset();
  getOverview.mockReset();
  createStarter.mockReset();
  getOverview.mockResolvedValue(overview());
});

describe("Analytics", () => {
  it("computes from real counts only", async () => {
    getOps.mockResolvedValue(opsFixture());
    render(<Page />);
    expect(await screen.findByRole("heading", { level: 1, name: "Analytics" })).toBeInTheDocument();
    expect(screen.getByText("Total leads").nextSibling).toHaveTextContent("4");
    const convertedStat = screen.getAllByText("Converted").find((e) => e.className.includes("klaros-label"))!;
    expect(convertedStat.nextSibling).toHaveTextContent("1");
    expect(screen.getByText("25% of all leads")).toBeInTheDocument(); // 1 converted of 4
    expect(screen.getByRole("list", { name: "Leads by status" })).toHaveTextContent("Converted");
    expect(screen.getByRole("list", { name: "Leads by source" })).toHaveTextContent("Website");
    expect(screen.getByText("Patient enquiries").nextSibling).toHaveTextContent("2");
    expect(screen.getByText("Providers by country")).toBeInTheDocument();
  });

  it("with no leads: honest empty state, no percentage, nothing invented", async () => {
    getOps.mockResolvedValue(opsFixture({ leads: emptyLeads, module: { metrics: [], data: [], breakdowns: [] } }));
    render(<Page />);
    expect(await screen.findByText("No leads yet")).toBeInTheDocument();
    expect(screen.getByText(/No leads yet\. They appear here/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\d+% of all leads|NaN|Infinity/);
  });

  it("says plainly what it does not measure", async () => {
    getOps.mockResolvedValue(opsFixture());
    render(<Page />);
    expect(await screen.findByText(/Not measured: revenue, orders, website traffic and response time/)).toBeInTheDocument();
  });

  it("shows a load error with a retry", async () => {
    getOps.mockImplementation(async () => { throw new Error("down"); });
    render(<Page />);
    expect(await screen.findByText(/couldn't load your operations/)).toBeInTheDocument();
  });
});


describe("Analytics — AI workforce", () => {
  it("says it is not measured when nothing was recorded", async () => {
    getOps.mockResolvedValue(opsFixture());
    render(<Page />);
    expect(await screen.findByText(/Not measured — no AI conversations have been recorded/)).toBeInTheDocument();
  });

  it("shows recorded AI figures only from real counts", async () => {
    getOps.mockResolvedValue(opsFixture({ ai: { events: {}, interactions: 5, qualified: 3, escalated: 1, needs_person: 1 } }));
    render(<Page />);
    const sec = (await screen.findByRole("heading", { name: "AI workforce" })).closest("section")!;
    expect(within(sec).getByText("Conversations").nextSibling).toHaveTextContent("5");
    expect(within(sec).getByText("Qualified by AI").nextSibling).toHaveTextContent("3");
    expect(within(sec).getByText("Escalated to a person").nextSibling).toHaveTextContent("1");
  });

  it("shows industry breakdowns such as top treatments and destinations", async () => {
    getOps.mockResolvedValue(opsFixture({ module: { metrics: [], data: [], breakdowns: [{ key: "top_procedures", label: "Most requested treatments", items: [{ label: "Hair transplant", value: 3 }] }, { key: "top_destinations", label: "Preferred destinations", items: [{ label: "TR", value: 2 }] }] } }));
    render(<Page />);
    expect(await screen.findByText("Most requested treatments")).toBeInTheDocument();
    expect(screen.getByText("Hair transplant")).toBeInTheDocument();
    expect(screen.getByText("Preferred destinations")).toBeInTheDocument();
  });
});
