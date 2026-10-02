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
    expect(await screen.findByText(/Revenue, orders and website traffic are not measured/)).toBeInTheDocument();
  });

  it("shows a load error with a retry", async () => {
    getOps.mockImplementation(async () => { throw new Error("down"); });
    render(<Page />);
    expect(await screen.findByText(/couldn't load your operations/)).toBeInTheDocument();
  });
});
