import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { emptyLeads, opsFixture } from "@/lib/__tests__/opsFixtures";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }), usePathname: () => "/business/data" }));
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
import Page from "@/app/business/data/page";

beforeEach(() => {
  getOps.mockReset();
  getOverview.mockReset();
  createStarter.mockReset();
  getOverview.mockResolvedValue(overview());
});

const planned = (key: string, label: string) => ({ key, label, required: true, klaros_support: "PLANNED", providers: [] });

describe("Data", () => {
  it("shows real counts for leads, customers and the industry data, linked to their screens", async () => {
    getOps.mockResolvedValue(opsFixture());
    render(<Page />);
    expect(await screen.findByRole("heading", { level: 1, name: "Data" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Leads\s*4/ })).toHaveAttribute("href", "/leads");
    expect(screen.getByRole("link", { name: /Customers\s*1/ })).toHaveAttribute("href", "/customers");
    expect(screen.getByRole("link", { name: /Providers\s*2/ })).toHaveAttribute("href", "/medical-tourism/providers");
    expect(screen.getByRole("link", { name: /Procedures\s*0\s*nothing yet/ })).toBeInTheDocument(); // honest zero
  });

  it("lists required-but-unmodelled data as Not configured, with no sample rows", async () => {
    getOps.mockResolvedValue(opsFixture({ module: { metrics: [], data: [], breakdowns: [] } }));
    getOverview.mockResolvedValue(overview([planned("inventory", "Inventory"), planned("product_catalog", "Product catalog")]));
    render(<Page />);
    const sec = (await screen.findByRole("heading", { name: "Not configured" })).closest("section")!;
    expect(sec).toHaveTextContent("Inventory");
    expect(sec).toHaveTextContent("Planned");
    expect(screen.queryByRole("heading", { name: "Your industry data" })).not.toBeInTheDocument();
  });

  it("an empty business shows zeros, not invented data", async () => {
    getOps.mockResolvedValue(opsFixture({ leads: emptyLeads, customers: 0, module: { metrics: [], data: [], breakdowns: [] } }));
    render(<Page />);
    expect(await screen.findByRole("link", { name: /Leads\s*0\s*nothing yet/ })).toBeInTheDocument();
  });

  it("shows a load error with a retry", async () => {
    getOps.mockImplementation(async () => { throw new Error("down"); });
    render(<Page />);
    expect(await screen.findByText(/couldn't load your operations/)).toBeInTheDocument();
  });
});
