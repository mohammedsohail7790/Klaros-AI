import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { emptyLeads, opsFixture } from "@/lib/__tests__/opsFixtures";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }), usePathname: () => "/business/integrations" }));
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
import Page from "@/app/business/integrations/page";

beforeEach(() => {
  getOps.mockReset();
  getOverview.mockReset();
  createStarter.mockReset();
  getOverview.mockResolvedValue(overview());
});

describe("Integration Center", () => {
  it("groups integrations and derives each state honestly", async () => {
    getOps.mockResolvedValue(opsFixture());
    render(<Page />);
    expect(await screen.findByRole("heading", { level: 1, name: "Integrations" })).toBeInTheDocument();
    expect(screen.getByText("1 connected · 1 available to connect.")).toBeInTheDocument();
    const stripe = screen.getByRole("heading", { name: "Stripe" }).closest("li")!;
    expect(stripe).toHaveTextContent("Available — not connected");
    expect(within(stripe).getByRole("link", { name: "Connect Stripe" })).toHaveAttribute("href", "/settings/integrations");
    expect(screen.getByRole("heading", { name: "Google Calendar" }).closest("li")).toHaveTextContent("Connected");
  });

  it("offers no connect action where connecting is impossible (planned adapter / Halla)", async () => {
    getOps.mockResolvedValue(opsFixture());
    render(<Page />);
    const ads = (await screen.findByRole("heading", { name: "Google Ads" })).closest("li")!;
    expect(ads).toHaveTextContent("Planned");
    expect(within(ads).queryByRole("link")).not.toBeInTheDocument();
    const halla = screen.getByRole("heading", { name: "Halla AI" }).closest("li")!;
    expect(halla).toHaveTextContent("Integration required");
    expect(halla).toHaveTextContent(/Needs the integration to be built/);
    expect(halla).toHaveTextContent("Not possible yet"); // never "Connected"
    // the only action is to review the (Klaros-side) setup — there is nothing to connect or sign in to
    expect(within(halla).getByRole("link", { name: /Review setup/ })).toHaveAttribute("href", "/workforce");
    expect(within(halla).queryByRole("link", { name: /connect|sign in|manage/i })).not.toBeInTheDocument();
  });

  it("marks what the business's own requirements need, from the Requirements model", async () => {
    getOps.mockResolvedValue(opsFixture());
    getOverview.mockResolvedValue(overview([{ key: "payment_processing", required: true, providers: [{ provider_key: "stripe" }] }]));
    render(<Page />);
    const stripe = (await screen.findByRole("heading", { name: "Stripe" })).closest("li")!;
    await waitFor(() => expect(stripe).toHaveTextContent("Your business needs this"));
    expect(screen.getByRole("heading", { name: "Google Calendar" }).closest("li")).not.toHaveTextContent("Your business needs this");
  });

  it("shows an empty group as 'Nothing available yet' (Planned), not as a fake integration", async () => {
    getOps.mockResolvedValue(opsFixture());
    render(<Page />);
    const sec = (await screen.findByRole("heading", { name: "CRM" })).closest("section")!;
    expect(sec).toHaveTextContent("Nothing available yet");
    const comms = (await screen.findByRole("heading", { name: "Communication" })).closest("section")!;
    expect(comms).toHaveTextContent(/No email or messaging provider is catalogued yet/);
    expect(within(comms).getByRole("link", { name: "AI workforce" })).toHaveAttribute("href", "/workforce");
  });

  it("surfaces a connection error and a load failure", async () => {
    getOps.mockResolvedValue(opsFixture({ integrations: [{ provider_key: "stripe", name: "Stripe", category: "Payments", purpose: null, capabilities: [], state: "CONFIGURATION_REQUIRED", implementation: "REAL", last_error: "Invalid API key" }] }));
    const { unmount } = render(<Page />);
    expect(await screen.findByText("The connection needs attention: Invalid API key")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Fix connection Stripe" })).toBeInTheDocument();
    unmount();
    getOps.mockImplementation(async () => { throw new Error("down"); });
    render(<Page />);
    expect(await screen.findByText(/couldn't load your operations/)).toBeInTheDocument();
  });
});


describe("Integration Center — purpose, connection, requirement, action on every card", () => {
  it("says what each integration is for, its connection, what it needs and the one action", async () => {
    getOps.mockResolvedValue(opsFixture());
    render(<Page />);
    const stripe = (await screen.findByRole("heading", { name: "Stripe" })).closest("li")!;
    expect(stripe).toHaveTextContent("Payments.");
    expect(stripe).toHaveTextContent("Not connected");
    expect(stripe).toHaveTextContent("Needs your account");
    const cal = screen.getByRole("heading", { name: "Google Calendar" }).closest("li")!;
    expect(cal).toHaveTextContent("Nothing to configure.");
    expect(within(cal).getByRole("link", { name: "Manage Google Calendar" })).toBeInTheDocument();
  });

  it("uses the product's category names", async () => {
    getOps.mockResolvedValue(opsFixture({ integration_groups: ["CRM & operations", "Communication", "Calendar", "Payments", "Accounting", "Marketing", "Ecommerce", "Analytics", "AI Workforce"] }));
    render(<Page />);
    await screen.findByRole("heading", { level: 1, name: "Integrations" });
    const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(headings).toEqual(["CRM", "Communication", "Calendar", "Payments", "Finance", "Marketing", "Ecommerce", "Analytics", "AI Workforce"]);
  });
});
